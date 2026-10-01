/**
 * 云函数：course  课程管理
 * ------------------------------------------------------------------
 * action:
 *   list    课程列表（首页用 upcoming，后台用 all）
 *   detail  课程详情
 *   create  新增课程（管理员）
 *   update  编辑课程（管理员）
 *   remove  删除课程（管理员，若已有有效预约需 force）
 * ------------------------------------------------------------------
 */
const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()
const _ = db.command

const ok = (data) => ({ code: 0, data, message: 'ok' })
const fail = (message, code = 1) => ({ code, data: null, message })

function bizErr(message, code = 1) {
  const err = new Error(message)
  err.isBiz = true
  err.code = code
  return err
}

/** 权限校验：只有管理员能操作 */
async function assertAdmin(openid) {
  const res = await db.collection('users').where({ openid }).get()
  const user = res.data[0]
  if (!user || user.role !== 'admin') throw bizErr('无权限操作，仅管理员可用', 403)
  return user
}

/** '2026-10-01' + '09:30' → Date（本地时区） */
function parseDateTime(date, time) {
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(date || '')
  const t = /^(\d{1,2}):(\d{1,2})$/.exec(time || '')
  if (!m || !t) return null
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(t[1]), Number(t[2]), 0, 0)
  return isNaN(d.getTime()) ? null : d
}

const WEEK_TEXT = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/** 校验并组装课程字段 */
function buildCourseData(event) {
  const name = String(event.name || '').trim()
  const type = String(event.type || '').trim()
  const teacher = String(event.teacher || '').trim()
  const location = String(event.location || '').trim()
  const desc = String(event.desc || '').trim().slice(0, 500)
  const capacity = parseInt(event.capacity, 10)
  const duration = parseInt(event.duration, 10)
  const status = event.status === 'closed' ? 'closed' : 'open'

  if (!name) throw bizErr('请填写课程名称')
  if (!type) throw bizErr('请选择瑜伽类型')
  if (!teacher) throw bizErr('请填写授课老师')
  if (!location) throw bizErr('请填写上课地点')
  if (!capacity || capacity < 1 || capacity > 200) throw bizErr('容纳人数需为 1-200')
  if (!duration || duration < 15 || duration > 300) throw bizErr('课程时长需为 15-300 分钟')

  const start = parseDateTime(event.date, event.time)
  if (!start) throw bizErr('请选择正确的上课时间')

  return {
    name,
    type,
    teacher,
    location,
    desc,
    capacity,
    duration,
    status,
    startTime: start.getTime(),
    endTime: start.getTime() + duration * 60000,
    startTimeStr: `${event.date} ${event.time.length === 5 ? event.time : '0' + event.time}`,
    dateKey: event.date,
    weekday: WEEK_TEXT[start.getDay()],
    updateTime: Date.now()
  }
}

/* ==================== 课程列表 ==================== */
async function list(event) {
  const where = {}

  // 时间范围：首页默认「从现在开始」，后台传 includePast 查全部
  let cond = null
  if (!event.includePast) cond = _.gte(event.from || Date.now())
  if (event.to) cond = cond ? cond.and(_.lte(event.to)) : _.lte(event.to)
  if (cond) where.startTime = cond

  if (event.status) where.status = event.status

  const limit = Math.min(parseInt(event.limit, 10) || 20, 50)
  const skip = parseInt(event.skip, 10) || 0
  const order = event.order === 'desc' ? 'desc' : 'asc'

  const countRes = await db.collection('course').where(where).count()
  const listRes = await db
    .collection('course')
    .where(where)
    .orderBy('startTime', order)
    .skip(skip)
    .limit(limit)
    .get()

  return ok({ total: countRes.total, list: listRes.data })
}

/* ==================== 课程详情 ==================== */
async function detail(event) {
  if (!event.id) throw bizErr('缺少课程 ID')
  const res = await db.collection('course').doc(event.id).get()
  if (!res.data) throw bizErr('课程不存在或已删除')
  return ok({ course: res.data })
}

/* ==================== 新增课程 ==================== */
async function create(event) {
  const data = buildCourseData(event)
  data.booked = 0
  data.createTime = Date.now()
  const res = await db.collection('course').add({ data })
  return ok({ _id: res._id })
}

/* ==================== 编辑课程 ==================== */
async function update(event) {
  if (!event.id) throw bizErr('缺少课程 ID')
  const data = buildCourseData(event)

  // 已有预约人数不能小于 0，且不能超过新容量
  const old = await db.collection('course').doc(event.id).get()
  if (!old.data) throw bizErr('课程不存在或已删除')
  if (data.capacity < (old.data.booked || 0)) {
    throw bizErr(`容纳人数不能小于已预约人数（${old.data.booked} 人）`)
  }

  await db.collection('course').doc(event.id).update({ data })
  return ok({})
}

/* ==================== 删除课程 ==================== */
async function remove(event) {
  if (!event.id) throw bizErr('缺少课程 ID')

  const active = await db
    .collection('order')
    .where({ courseId: event.id, status: _.in(['booked']) })
    .count()

  // 还有未处理的预约：第一次调用时提示，传 force=true 才真正删除
  if (active.total > 0 && !event.force) {
    return fail(`该课程还有 ${active.total} 条待处理预约，删除后将自动取消这些预约，确定继续吗？`, 4001)
  }

  // 有预约时：把这些预约置为已取消
  if (active.total > 0) {
    await db
      .collection('order')
      .where({ courseId: event.id, status: 'booked' })
      .update({ data: { status: 'canceled', cancelTime: Date.now(), cancelReason: '课程已取消' } })
  }

  await db.collection('course').doc(event.id).remove()
  return ok({ canceledOrders: active.total })
}

exports.main = async (event) => {
  const { OPENID } = cloud.getWXContext()
  const action = event.action || 'list'

  try {
    switch (action) {
      case 'list':
        return await list(event)
      case 'detail':
        return await detail(event)
      case 'create':
        await assertAdmin(OPENID)
        return await create(event)
      case 'update':
        await assertAdmin(OPENID)
        return await update(event)
      case 'remove':
        await assertAdmin(OPENID)
        return await remove(event)
      default:
        return fail('未知操作：' + action, 404)
    }
  } catch (err) {
    console.error('[course] 执行出错', err)
    return fail(err.isBiz ? err.message : '服务异常，请稍后重试')
  }
}
