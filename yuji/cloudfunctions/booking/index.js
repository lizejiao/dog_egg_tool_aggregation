/**
 * 云函数：booking  预约 / 取消 / 签到
 * ------------------------------------------------------------------
 * action:
 *   create    学员预约课程（事务：防重复、防超卖、校验课时）
 *   cancel    取消预约（本人或管理员）
 *   listMine  我的预约记录
 *   listAll   全部预约记录（管理员，带会员信息）
 *   signin    管理员为预约签到（事务：改状态 + 扣课时 + 写签到/流水）
 * ------------------------------------------------------------------
 * 消课规则：签到成功 = 消耗 1 课时（utils/config.js 的 HOURS_PER_CLASS）
 */
const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()
const _ = db.command

// 与小程序端 utils/config.js 保持一致
const HOURS_PER_CLASS = 1

const ok = (data) => ({ code: 0, data, message: 'ok' })
const fail = (message, code = 1) => ({ code, data: null, message })

function bizErr(message, code = 1) {
  const err = new Error(message)
  err.isBiz = true
  err.code = code
  return err
}

async function assertAdmin(openid) {
  const res = await db.collection('users').where({ openid }).get()
  const user = res.data[0]
  if (!user || user.role !== 'admin') throw bizErr('无权限操作，仅管理员可用', 403)
  return user
}

/* ==================== 预约课程 ==================== */
async function create(event, openid) {
  if (!event.courseId) throw bizErr('缺少课程 ID')

  const transaction = await db.startTransaction()
  try {
    // 1. 课程校验
    const courseRes = await transaction.collection('course').doc(event.courseId).get()
    const course = courseRes.data
    if (!course) throw bizErr('课程不存在或已删除')
    if (course.status !== 'open') throw bizErr('该课程已停课，无法预约')
    if (!course.startTime || course.startTime <= Date.now()) throw bizErr('该课程已开始或已结束，无法预约')
    if ((course.booked || 0) >= course.capacity) throw bizErr('名额已满，无法预约')

    // 2. 同一课程不能重复预约（booked / signed 都算有效预约）
    const dupRes = await transaction.collection('order').where({ openid, courseId: event.courseId }).get()
    const duplicated = (dupRes.data || []).some((o) => o.status === 'booked' || o.status === 'signed')
    if (duplicated) throw bizErr('您已预约过该课程，请勿重复预约')

    // 3. 课时校验：课时不足不能预约
    const userRes = await transaction.collection('users').where({ openid }).get()
    const user = userRes.data[0]
    if (!user) throw bizErr('会员档案不存在，请重新进入小程序')
    if ((user.balance || 0) < HOURS_PER_CLASS) {
      throw bizErr('剩余课时不足，请先购买课时或联系馆主充值')
    }

    // 4. 写预约记录（带课程快照，课程被删后仍可展示）
    const now = Date.now()
    const orderData = {
      openid,
      courseId: event.courseId,
      courseName: course.name,
      courseType: course.type,
      teacher: course.teacher,
      location: course.location,
      startTime: course.startTime,
      endTime: course.endTime,
      startTimeStr: course.startTimeStr || '',
      status: 'booked', // booked 已预约 / signed 已签到 / canceled 已取消
      createTime: now,
      updateTime: now,
      signinTime: null,
      cancelTime: null
    }
    await transaction.collection('order').add({ data: orderData })

    // 5. 课程已预约人数 +1
    await transaction
      .collection('course')
      .doc(event.courseId)
      .update({ data: { booked: _.inc(1), updateTime: now } })

    await transaction.commit()
    return ok({ orderId: '' })
  } catch (e) {
    await transaction.rollback()
    throw e
  }
}

/* ==================== 取消预约 ==================== */
async function cancel(event, openid, isAdminAction) {
  if (!event.orderId) throw bizErr('缺少预约 ID')

  const res = await db.collection('order').doc(event.orderId).get()
  const order = res.data
  if (!order) throw bizErr('预约记录不存在')
  // 学员只能取消自己的预约
  if (!isAdminAction && order.openid !== openid) throw bizErr('无权操作该预约', 403)
  if (order.status === 'canceled') throw bizErr('该预约已取消')
  if (order.status === 'signed') throw bizErr('已签到的课程不能取消，请直接扣课时处理')

  const now = Date.now()
  await db
    .collection('order')
    .doc(order._id)
    .update({ data: { status: 'canceled', cancelTime: now, updateTime: now, cancelBy: isAdminAction ? 'admin' : 'self' } })

  // 释放名额（课程可能已被删除，做容错）
  if (order.courseId) {
    try {
      await db.collection('course').doc(order.courseId).update({ data: { booked: _.inc(-1) } })
    } catch (e) {
      console.warn('[booking] 释放名额失败（课程可能已删除）', e)
    }
  }
  return ok({})
}

/* ==================== 我的预约 ==================== */
async function listMine(event, openid) {
  const where = { openid }
  if (event.status) where.status = event.status
  if (event.courseId) where.courseId = event.courseId

  const limit = Math.min(parseInt(event.limit, 10) || 50, 100)
  const res = await db
    .collection('order')
    .where(where)
    .orderBy('startTime', 'desc')
    .limit(limit)
    .get()

  return ok({ list: res.data })
}

/* ==================== 我的签到记录 ==================== */
async function mySignins(event, openid) {
  const limit = Math.min(parseInt(event.limit, 10) || 50, 100)
  const res = await db
    .collection('signin')
    .where({ openid })
    .orderBy('signinTime', 'desc')
    .limit(limit)
    .get()
  return ok({ list: res.data })
}

/* ==================== 全部预约（管理员） ==================== */
async function listAll(event) {
  const where = {}
  if (event.status) where.status = event.status
  if (event.courseId) where.courseId = event.courseId

  const limit = Math.min(parseInt(event.limit, 10) || 30, 100)
  const skip = parseInt(event.skip, 10) || 0

  const countRes = await db.collection('order').where(where).count()
  const res = await db
    .collection('order')
    .where(where)
    .orderBy('createTime', 'desc')
    .skip(skip)
    .limit(limit)
    .get()

  // 批量带出会员昵称 / 手机号，方便管理端展示
  const openids = Array.from(new Set((res.data || []).map((o) => o.openid)))
  let userMap = {}
  if (openids.length) {
    const usersRes = await db.collection('users').where({ openid: _.in(openids) }).limit(100).get()
    userMap = (usersRes.data || []).reduce((map, u) => {
      map[u.openid] = { nickName: u.nickName || '未设置昵称', phone: u.phone || '', balance: u.balance || 0 }
      return map
    }, {})
  }

  return ok({ total: countRes.total, list: res.data, userMap })
}

/* ==================== 签到（管理员） ==================== */
async function signin(event, adminOpenid) {
  if (!event.orderId) throw bizErr('缺少预约 ID')
  const now = Date.now()

  const transaction = await db.startTransaction()
  try {
    // 1. 预约记录校验
    const orderRes = await transaction.collection('order').doc(event.orderId).get()
    const order = orderRes.data
    if (!order) throw bizErr('预约记录不存在')
    if (order.status === 'signed') throw bizErr('该会员已签到，请勿重复操作')
    if (order.status === 'canceled') throw bizErr('该预约已取消，无法签到')

    // 2. 会员校验（课时不足时阻止签到，提示先充值）
    const userRes = await transaction.collection('users').where({ openid: order.openid }).get()
    const user = userRes.data[0]
    if (!user) throw bizErr('会员档案不存在')
    if ((user.balance || 0) < HOURS_PER_CLASS) {
      throw bizErr(`会员「${user.nickName || '未设置昵称'}」剩余课时不足，请先为其增加课时`)
    }

    // 3. 预约状态 → 已签到
    await transaction
      .collection('order')
      .doc(event.orderId)
      .update({ data: { status: 'signed', signinTime: now, updateTime: now, operatorOpenid: adminOpenid } })

    // 4. 扣课时，并同步套餐使用进度（优先消耗先到期的套餐）
    const packages = (user.packages || []).map((p) => Object.assign({}, p))
    let target = null
    for (const p of packages) {
      const notUsedUp = (p.usedHours || 0) < p.totalHours
      const notExpired = !p.expireAt || p.expireAt > now
      if (notUsedUp && notExpired && (!target || (p.expireAt || Infinity) < (target.expireAt || Infinity))) {
        target = p
      }
    }
    if (target) target.usedHours = (target.usedHours || 0) + HOURS_PER_CLASS

    await transaction
      .collection('users')
      .doc(user._id)
      .update({ data: { balance: _.inc(-HOURS_PER_CLASS), packages, updateTime: now } })

    // 5. 签到记录（dateKey 用于按天统计消课）
    const d = new Date(now)
    const p2 = (n) => (n < 10 ? '0' + n : '' + n)
    const dateKey = `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`

    await transaction.collection('signin').add({
      data: {
        openid: user.openid,
        userId: user._id,
        nickName: user.nickName || '',
        courseId: order.courseId,
        orderId: order._id,
        courseName: order.courseName,
        courseType: order.courseType || '',
        teacher: order.teacher || '',
        startTime: order.startTime,
        startTimeStr: order.startTimeStr || '',
        hours: HOURS_PER_CLASS,
        dateKey,
        signinTime: now,
        operatorOpenid: adminOpenid
      }
    })

    // 6. 课时流水（消课统计的数据来源）
    await transaction.collection('hourslog').add({
      data: {
        openid: user.openid,
        nickName: user.nickName || '',
        type: 'signin',
        amount: -HOURS_PER_CLASS,
        balanceAfter: (user.balance || 0) - HOURS_PER_CLASS,
        reason: order.courseName || '课程签到',
        courseId: order.courseId,
        orderId: order._id,
        operatorOpenid: adminOpenid,
        createTime: now
      }
    })

    await transaction.commit()
    return ok({})
  } catch (e) {
    await transaction.rollback()
    throw e
  }
}

exports.main = async (event) => {
  const { OPENID } = cloud.getWXContext()
  const action = event.action || 'listMine'

  try {
    switch (action) {
      case 'create':
        return await create(event, OPENID)
      case 'cancel': {
        // 管理员可以代学员取消
        const userRes = await db.collection('users').where({ openid: OPENID }).get()
        const isAdmin = userRes.data[0] && userRes.data[0].role === 'admin'
        return await cancel(event, OPENID, isAdmin)
      }
      case 'listMine':
        return await listMine(event, OPENID)
      case 'mySignins':
        return await mySignins(event, OPENID)
      case 'listAll':
        await assertAdmin(OPENID)
        return await listAll(event)
      case 'signin':
        await assertAdmin(OPENID)
        return await signin(event, OPENID)
      default:
        return fail('未知操作：' + action, 404)
    }
  } catch (err) {
    console.error('[booking] 执行出错', err)
    return fail(err.isBiz ? err.message : '服务异常，请稍后重试')
  }
}
