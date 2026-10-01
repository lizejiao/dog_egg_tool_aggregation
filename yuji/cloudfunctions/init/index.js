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

const ok = (data) => ({ code: 0, data, message: 'ok' })
const fail = (message, code = 1) => ({ code, data: null, message })

const COLLECTIONS = ['users', 'course', 'order', 'package', 'signin', 'notice', 'hourslog']

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
      { title: '欢迎来到瑜伽馆', content: '首次使用请先在「我的 → 课时套餐」了解套餐，联系馆主开通课时后即可预约课程。', createTime: Date.now(), updateTime: Date.now() },
      { title: '温馨提示', content: '请提前 10 分钟到馆，自备瑜伽垫与水杯；如无法到课请提前在「我的预约」中取消，方便其他会员预约。', createTime: Date.now() - 86400000, updateTime: Date.now() - 86400000 }
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

exports.main = async (event) => {
  try {
    const collections = await ensureCollections()
    const seeded = await seed(!!event.forceSeed)
    let adminSet = 0
    if (event.setAdminOpenid) {
      adminSet = await setAdmin(String(event.setAdminOpenid).trim())
    }

    return ok({
      createdCollections: collections,
      seeded,
      adminSet: adminSet > 0,
      tip: event.setAdminOpenid ? '管理员已设置' : '如需设置管理员，可在调用时传入 setAdminOpenid'
    })
  } catch (err) {
    console.error('[init] 执行出错', err)
    return fail(err.message || '初始化失败')
  }
}
