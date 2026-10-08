/**
 * 云函数：init  一键初始化（首次部署时在开发者工具里手动调用一次）
 * ------------------------------------------------------------------
 * 功能：
 *   1. 创建数据库集合（users / course / order / package / signin / notice / hourslog）
 *   2. 写入演示数据：课时套餐、近期课程、公告
 *   3. 可选：把指定 openid 设为管理员（event.setAdminOpenid）
 *
 * 幂等：集合已存在会跳过；已有课程/套餐数据时不重复写入演示数据。
 * 调用方式（开发者工具 → 云开发 → 云函数 → init → 云端测试）：
 *   { "action": "run" }
 * ------------------------------------------------------------------
 */
const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()
const _ = db.command

/* 超管手机号名单（与 login 云函数保持一致） */
const SUPER_ADMIN_PHONES = ['17611028882']

const ok = (data) => ({ code: 0, data, message: 'ok' })
const fail = (message, code = 1) => ({ code, data: null, message })

const COLLECTIONS = ['users', 'course', 'order', 'package', 'signin', 'notice', 'hourslog', 'gyms', 'invitations', 'gym_members']

const WEEK_TEXT = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function pad(n) {
  return n < 10 ? '0' + n : '' + n
}

/** 生成 yyyy-mm-dd（以今天为基准偏移 offset 天） */
function dateStr(offset) {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 组装一条课程记录 */
function makeCourse(offsetDay, time, name, type, teacher, location, capacity, duration, desc) {
  const [y, m, d] = dateStr(offsetDay).split('-').map(Number)
  const [hh, mm] = time.split(':').map(Number)
  const start = new Date(y, m - 1, d, hh, mm, 0, 0)
  return {
    name,
    type,
    teacher,
    location,
    desc: desc || '',
    capacity,
    booked: 0,
    duration,
    status: 'open',
    startTime: start.getTime(),
    endTime: start.getTime() + duration * 60000,
    startTimeStr: `${dateStr(offsetDay)} ${time}`,
    dateKey: dateStr(offsetDay),
    weekday: WEEK_TEXT[start.getDay()],
    createTime: Date.now(),
    updateTime: Date.now()
  }
}

/* ==================== 创建集合 ==================== */
async function ensureCollections() {
  const created = []
  for (const name of COLLECTIONS) {
    try {
      await db.createCollection(name)
      created.push(name)
    } catch (e) {
      // 已存在会抛错，忽略即可
      console.log(`[init] 集合 ${name} 已存在或创建失败：`, e.errCode || e.message)
    }
  }
  return created
}

/* ==================== 演示数据 ==================== */
async function seed(force) {
  const result = { packages: 0, courses: 0, notices: 0 }

  // 课时套餐
  if (force || (await db.collection('package').count()).total === 0) {
    const packages = [
      { name: '体验套餐', totalHours: 3, price: 199, validDays: 30, desc: '新会员专享，30 天内有效' },
      { name: '月卡套餐', totalHours: 12, price: 899, validDays: 30, desc: '每周 3 节刚刚好' },
      { name: '季卡套餐', totalHours: 40, price: 2680, validDays: 90, desc: '长期练习更划算' }
    ].map((p) => Object.assign({ createTime: Date.now(), updateTime: Date.now() }, p))
    for (const p of packages) {
      await db.collection('package').add({ data: p })
      result.packages++
    }
  }

  // 近期课程
  if (force || (await db.collection('course').count()).total === 0) {
    const courses = [
      makeCourse(0, '19:30', '晚间阴瑜伽', '阴瑜伽', 'Luna', 'A 教室（大班）', 12, 60, '舒缓拉伸，放松身心，适合一天疲劳后的修复练习。'),
      makeCourse(1, '09:30', '晨间流瑜伽', '流瑜伽', 'Mia', 'A 教室（大班）', 12, 75, '呼吸与体式流动结合，唤醒身体，开启元气一天。'),
      makeCourse(1, '15:00', '空中瑜伽入门', '空中瑜伽', 'Coco', '空中瑜伽室', 8, 60, '借助吊床完成体式，改善脊柱压力，零基础可练。'),
      makeCourse(2, '10:00', '普拉提塑形', '普拉提', 'Ella', 'B 教室（小班）', 10, 60, '核心激活与体态矫正，小班课纠正更细致。'),
      makeCourse(2, '19:00', '哈他瑜伽基础', '哈他瑜伽', 'Luna', 'A 教室（大班）', 15, 60, '经典体式讲解，节奏平缓，适合初学者。')
    ]
    for (const c of courses) {
      await db.collection('course').add({ data: c })
      result.courses++
    }
  }

  // 公告
  if (force || (await db.collection('notice').count()).total === 0) {
    const notices = [
      { gymId: '', title: '欢迎来到瑜伽馆', content: '首次使用请先在「我的 → 课时套餐」了解套餐，联系馆主开通课时后即可预约课程。', createTime: Date.now(), updateTime: Date.now() },
      { gymId: '', title: '温馨提示', content: '请提前 10 分钟到馆，自备瑜伽垫与水杯；如无法到课请提前在「我的预约」中取消，方便其他会员预约。', createTime: Date.now() - 86400000, updateTime: Date.now() - 86400000 }
    ]
    for (const n of notices) {
      await db.collection('notice').add({ data: n })
      result.notices++
    }
  }

  return result
}

/* ==================== 设置管理员 ==================== */
async function setAdmin(openid) {
  if (!openid) return 0
  const res = await db.collection('users').where({ openid }).get()
  if (res.data[0]) {
    await db.collection('users').doc(res.data[0]._id).update({ data: { role: 'admin', updateTime: Date.now() } })
  } else {
    await db.collection('users').add({
      data: {
        openid,
        nickName: '馆主',
        avatarUrl: '',
        phone: '',
        role: 'admin',
        balance: 0,
        packages: [],
        createTime: Date.now(),
        updateTime: Date.now()
      }
    })
  }
  return 1
}

/* ==================== 默认馆迁移（多馆平台升级，幂等可重复执行） ==================== */
const DEFAULT_GYM_NAME = '悦己瑜伽（默认馆）'

async function migrate() {
  const result = { gym: '', ownerSet: false, updated: { course: 0, order: 0, package: 0, notice: 0, signin: 0, hourslog: 0, userPackages: 0 } }

  // 1. 找到或创建默认馆
  let gymRes = await db.collection('gyms').where({ name: DEFAULT_GYM_NAME }).get()
  let gym = gymRes.data[0]
  if (!gym) {
    const now = Date.now()
    const added = await db.collection('gyms').add({
      data: {
        name: DEFAULT_GYM_NAME,
        city: '北京',
        address: '',
        phone: '',
        images: [],
        desc: '系统迁移生成的默认场馆',
        status: 'active',
        ownerOpenid: '',
        locateEnabled: false,
        latitude: 0,
        longitude: 0,
        courseCount: 0,
        createTime: now,
        updateTime: now
      }
    })
    gym = { _id: added._id }
    result.gym = added._id
  } else {
    result.gym = gym._id
  }
  const gymId = gym._id

  // 2. 存量数据回填 gymId（只更新没有 gymId 字段的记录）
  for (const col of ['course', 'order', 'package', 'notice', 'signin', 'hourslog']) {
    const updated = await db
      .collection(col)
      .where({ gymId: _.exists(false) })
      .update({ data: { gymId } })
    result.updated[col] = updated.stats ? updated.stats.updated : 0
  }

  // 2.5 预约记录回填/修正 dateKey（以课程快照时间为准；老回填可能误用了 createTime）
  const ordersAll = await db.collection('order').limit(1000).get()
  const p2 = (n) => (n < 10 ? '0' + n : '' + n)
  const keyFromTime = (ts) => {
    const d = new Date(ts)
    return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
  }
  for (const o of ordersAll.data || []) {
    // 优先课程快照里的日期字符串，其次 startTime 时间戳
    const fromStr = o.startTimeStr ? String(o.startTimeStr).split(' ')[0] : ''
    const correctKey = fromStr || (o.startTime ? keyFromTime(o.startTime) : '')
    if (correctKey && o.dateKey !== correctKey) {
      await db.collection('order').doc(o._id).update({ data: { dateKey: correctKey } })
      result.updated.orderDateKeys = (result.updated.orderDateKeys || 0) + 1
    }
  }

  // 3. users.packages 内嵌套餐回填 gymId（逐用户处理）
  const usersRes = await db.collection('users').limit(1000).get()
  for (const u of usersRes.data) {
    const packs = u.packages || []
    const need = packs.some((p) => !p.gymId)
    if (need) {
      const newPacks = packs.map((p) => (p.gymId ? p : Object.assign({}, p, { gymId })))
      await db
        .collection('users')
        .doc(u._id)
        .update({ data: { packages: newPacks, updateTime: Date.now() } })
      result.updated.userPackages++
    }

    // 4. 角色修正（幂等，可重复执行）：
    //    手机号命中超管名单（无论当前是 admin 还是 owner）→ superadmin，并摘除名下场馆
    //    其余 admin → 默认馆馆主
    if (u.phone && SUPER_ADMIN_PHONES.indexOf(u.phone) > -1) {
      if (u.role !== 'superadmin') {
        await db.collection('users').doc(u._id).update({ data: { role: 'superadmin', updateTime: Date.now() } })
        await db
          .collection('gyms')
          .where({ ownerOpenid: u.openid })
          .update({ data: { ownerOpenid: '', updateTime: Date.now() } })
        result.superAdminFixed = true
      }
    } else if (u.role === 'admin') {
      await db.collection('users').doc(u._id).update({ data: { role: 'owner', updateTime: Date.now() } })
      await db.collection('gyms').doc(gymId).update({ data: { ownerOpenid: u.openid, updateTime: Date.now() } })
      result.ownerSet = true
    }
  }

  // 5. courseCount 统计
  const cnt = await db.collection('course').where({ gymId }).count()
  await db.collection('gyms').doc(gymId).update({ data: { courseCount: cnt.total, updateTime: Date.now() } })

  return result
}

exports.main = async (event) => {
  try {
    // 数据迁移（独立 action，幂等）
    if (event.action === 'migrate') {
      const migrated = await migrate()
      return ok({ migrated })
    }

    const collections = await ensureCollections()
    const seeded = await seed(!!event.forceSeed)
    // run 时顺带执行一次迁移（幂等，重复执行无副作用）
    const migrated = await migrate()

    return ok({
      createdCollections: collections,
      seeded,
      migrated,
      tip: '集合、演示数据与默认馆迁移均已完成'
    })
  } catch (err) {
    console.error('[init] 执行出错', err)
    return fail(err.message || '初始化失败')
  }
}
