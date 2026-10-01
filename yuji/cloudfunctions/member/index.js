/**
 * 云函数：member  会员管理 / 课时调整 / 课时统计
 * ------------------------------------------------------------------
 * action（除 profile 外均需管理员权限）:
 *   list      会员列表（支持昵称/手机号搜索）
 *   detail    会员详情 + 最近课时流水 + 最近签到
 *   adjust    手动增减课时（amount 正数=增加，负数=扣减）
 *   recharge  为会员开通课时套餐
 *   stats     课时统计（概览 + 会员消课排行 + 近 30 天趋势）
 * ------------------------------------------------------------------
 */
const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()
const _ = db.command
const $ = db.command.aggregate

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

function escapeReg(str) {
  return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** 本地日期 YYYY-MM-DD（服务器为东八区） */
function todayKey(ts) {
  const d = new Date(ts)
  const p = (n) => (n < 10 ? '0' + n : '' + n)
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** 掩码手机号 */
function maskPhone(phone) {
  if (!phone || phone.length < 7) return phone || ''
  return phone.replace(/^(\d{3})\d+(\d{4})$/, '$1****$2')
}

function maskUser(u) {
  return Object.assign({}, u, { phone: maskPhone(u.phone) })
}

/* ==================== 会员列表 ==================== */
async function list(event) {
  const limit = Math.min(parseInt(event.limit, 10) || 20, 100)
  const skip = parseInt(event.skip, 10) || 0
  const keyword = String(event.keyword || '').trim()

  let where = {}
  if (keyword) {
    const re = db.RegExp({ regexp: escapeReg(keyword), options: 'i' })
    where = _.or([{ nickName: re }, { phone: re }])
  }

  const countRes = await db.collection('users').where(where).count()
  const res = await db
    .collection('users')
    .where(where)
    .orderBy('createTime', 'desc')
    .skip(skip)
    .limit(limit)
    .get()

  return ok({ total: countRes.total, list: (res.data || []).map(maskUser) })
}

/* ==================== 会员详情 ==================== */
async function detail(event) {
  if (!event.openid) throw bizErr('缺少会员 openid')
  const userRes = await db.collection('users').where({ openid: event.openid }).get()
  const user = userRes.data[0]
  if (!user) throw bizErr('会员不存在')

  const [logs, signins] = await Promise.all([
    db.collection('hourslog').where({ openid: event.openid }).orderBy('createTime', 'desc').limit(50).get(),
    db.collection('signin').where({ openid: event.openid }).orderBy('signinTime', 'desc').limit(50).get()
  ])

  return ok({ user: maskUser(user), hoursLogs: logs.data, signins: signins.data })
}

/* ==================== 手动增减课时 ==================== */
async function adjust(event, adminOpenid) {
  const openid = String(event.openid || '')
  const amount = parseInt(event.amount, 10)
  const reason = String(event.reason || '').trim().slice(0, 50) || '管理员调整'

  if (!openid) throw bizErr('缺少会员 openid')
  if (!amount || isNaN(amount)) throw bizErr('请输入需要调整的课时数')
  if (Math.abs(amount) > 200) throw bizErr('单次调整不能超过 200 课时')

  const now = Date.now()
  const transaction = await db.startTransaction()
  try {
    const userRes = await transaction.collection('users').where({ openid }).get()
    const user = userRes.data[0]
    if (!user) throw bizErr('会员不存在')

    const newBalance = (user.balance || 0) + amount
    if (newBalance < 0) {
      throw bizErr(`课时不足：当前剩余 ${user.balance || 0} 课时，无法扣减 ${-amount} 课时`)
    }

    await transaction
      .collection('users')
      .doc(user._id)
      .update({ data: { balance: newBalance, updateTime: now } })

    await transaction.collection('hourslog').add({
      data: {
        openid,
        nickName: user.nickName || '',
        type: amount > 0 ? 'add' : 'deduct',
        amount,
        balanceAfter: newBalance,
        reason,
        operatorOpenid: adminOpenid,
        createTime: now,
        dateKey: todayKey(now)
      }
    })

    await transaction.commit()
    return ok({ balance: newBalance })
  } catch (e) {
    await transaction.rollback()
    throw e
  }
}

/* ==================== 为会员开通课时套餐 ==================== */
async function recharge(event, adminOpenid) {
  const openid = String(event.openid || '')
  if (!openid) throw bizErr('缺少会员 openid')
  if (!event.packageId) throw bizErr('请选择课时套餐')

  const now = Date.now()
  const pkgRes = await db.collection('package').doc(event.packageId).get()
  const pkg = pkgRes.data
  if (!pkg) throw bizErr('套餐不存在或已删除')

  const transaction = await db.startTransaction()
  try {
    const userRes = await transaction.collection('users').where({ openid }).get()
    const user = userRes.data[0]
    if (!user) throw bizErr('会员不存在')

    // 套餐有效期：validDays 天，0 表示长期有效
    const expireAt = pkg.validDays > 0 ? now + pkg.validDays * 86400000 : 0
    const entry = {
      packageId: pkg._id,
      name: pkg.name,
      totalHours: pkg.totalHours,
      usedHours: 0,
      price: pkg.price || 0,
      validDays: pkg.validDays || 0,
      boughtAt: now,
      expireAt
    }

    const packages = (user.packages || []).concat([entry])
    const newBalance = (user.balance || 0) + pkg.totalHours

    await transaction
      .collection('users')
      .doc(user._id)
      .update({ data: { packages, balance: newBalance, updateTime: now } })

    await transaction.collection('hourslog').add({
      data: {
        openid,
        nickName: user.nickName || '',
        type: 'recharge',
        amount: pkg.totalHours,
        balanceAfter: newBalance,
        reason: `购买套餐：${pkg.name}`,
        operatorOpenid: adminOpenid,
        createTime: now,
        dateKey: todayKey(now)
      }
    })

    await transaction.commit()
    return ok({ balance: newBalance })
  } catch (e) {
    await transaction.rollback()
    throw e
  }
}

/* ==================== 课时统计 ==================== */
async function stats() {
  const now = Date.now()
  const since = now - 30 * 86400000

  // 概览数据
  const [memberRes, bookedRes, signedRes, signinRes, addRes] = await Promise.all([
    db.collection('users').where({ role: 'student' }).count(),
    db.collection('order').where({ status: 'booked' }).count(),
    db.collection('order').where({ status: 'signed' }).count(),
    db.collection('signin').count(),
    db.collection('hourslog').where({ type: _.in(['recharge', 'add']) }).count()
  ])

  // 每位会员累计消课（签到记录聚合）
  const perMemberAgg = await db
    .collection('signin')
    .aggregate()
    .group({
      _id: '$openid',
      hours: $.sum('$hours'),
      times: $.sum(1),
      lastSigninTime: $.max('$signinTime')
    })
    .sort({ hours: -1 })
    .limit(200)
    .end()

  // 补充会员昵称 / 手机号 / 当前剩余课时
  const openids = (perMemberAgg.list || []).map((i) => i._id)
  let userMap = {}
  if (openids.length) {
    const usersRes = await db.collection('users').where({ openid: _.in(openids) }).limit(200).get()
    userMap = (usersRes.data || []).reduce((m, u) => {
      m[u.openid] = { nickName: u.nickName || '未设置昵称', phone: u.phone || '', balance: u.balance || 0 }
      return m
    }, {})
  }
  const perMember = (perMemberAgg.list || []).map((i) =>
    Object.assign({ openid: i._id }, i, userMap[i._id] || { nickName: '已注销会员', phone: '', balance: 0 })
  )

  // 近 30 天每日消课趋势
  const dailyAgg = await db
    .collection('signin')
    .aggregate()
    .match({ signinTime: _.gte(since) })
    .group({ _id: '$dateKey', count: $.sum(1) })
    .sort({ _id: 1 })
    .end()

  return ok({
    overview: {
      members: memberRes.total,
      bookedOrders: bookedRes.total,
      signedOrders: signedRes.total,
      totalSignins: signinRes.total,
      totalRecharge: addRes.total
    },
    perMember,
    daily: dailyAgg.list || []
  })
}

exports.main = async (event) => {
  const { OPENID } = cloud.getWXContext()
  const action = event.action || 'list'

  try {
    switch (action) {
      case 'list':
        await assertAdmin(OPENID)
        return await list(event)
      case 'detail':
        await assertAdmin(OPENID)
        return await detail(event)
      case 'adjust':
        await assertAdmin(OPENID)
        return await adjust(event, OPENID)
      case 'recharge':
        await assertAdmin(OPENID)
        return await recharge(event, OPENID)
      case 'stats':
        await assertAdmin(OPENID)
        return await stats()
      default:
        return fail('未知操作：' + action, 404)
    }
  } catch (err) {
    console.error('[member] 执行出错', err)
    return fail(err.isBiz ? err.message : '服务异常，请稍后重试')
  }
}
