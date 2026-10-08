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
  const role = (user && user.role) || ''
  if (!user || ['owner', 'superadmin', 'admin'].indexOf(role) === -1) throw bizErr('无权限操作，仅管理员可用', 403)
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
  const gymId = String(event.gymId || '').trim()

  // 指定了场馆：只返回已加入（approved）该馆的会员
  if (gymId) {
    let memberRes = await db
      .collection('gym_members')
      .where({ gymId, status: 'approved' })
      .orderBy('createTime', 'desc')
      .limit(100)
      .get()
    let members = memberRes.data || []

    // 关键词过滤（昵称/手机号）
    if (keyword) {
      const openids = members.map((m) => m.openid)
      if (!openids.length) return ok({ total: 0, list: [] })
      const re = db.RegExp({ regexp: escapeReg(keyword), options: 'i' })
      const usersRes = await db
        .collection('users')
        .where(_.and([{ openid: _.in(openids) }, _.or([{ nickName: re }, { phone: re }])]))
        .get()
      const kwSet = {}
      ;(usersRes.data || []).forEach((u) => {
        kwSet[u.openid] = u
      })
      members = members.filter((m) => kwSet[m.openid])
    }

    // 拉用户资料
    const openids = members.map((m) => m.openid)
    let userMap = {}
    if (openids.length) {
      const usersRes = await db.collection('users').where({ openid: _.in(openids) }).limit(100).get()
      userMap = (usersRes.data || []).reduce((m2, u) => {
        m2[u.openid] = u
        return m2
      }, {})
    }

    const list = members
      .filter((m) => userMap[m.openid])
      .map((m) => {
        const u = userMap[m.openid]
        return Object.assign({}, maskUser(u), {
          joinedAt: m.createTime,
          gymRole: 'member'
        })
      })

    return ok({ total: list.length, list: list.slice(skip, skip + limit) })
  }

  // 未指定场馆（超管视角）：全部注册用户
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

  // 服务端自动解析课时归属馆：
  //   操作者是馆主 → 其名下场馆（一人多馆暂取第一个）
  //   操作者是超管 → 不归属具体馆（全局）
  const adminRes = await db.collection('users').where({ openid: adminOpenid }).get()
  const adminUser = adminRes.data[0]
  let gymId = String(event.gymId || '').trim()
  if (!gymId && adminUser && adminUser.role === 'owner') {
    const gymRes = await db.collection('gyms').where({ ownerOpenid: adminOpenid }).limit(1).get()
    if (gymRes.data && gymRes.data.length) gymId = gymRes.data[0]._id
  }

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

    // 课时包账本操作（按馆隔离的核心）：
    //   增加 + 有归属馆 → 新增一条该馆的课时包记录
    //   扣减 → 从课时包里按「最早到期优先」扣，扣完不够再扣「无归属的裸课时」
    const packages = (user.packages || []).map((p) => Object.assign({}, p))

    if (amount > 0 && gymId) {
      packages.push({
        gymId,
        packageId: '',
        name: reason || '手动增加',
        totalHours: amount,
        usedHours: 0,
        price: 0,
        validDays: 0,
        boughtAt: now,
        expireAt: 0 // 长期有效
      })
    } else if (amount < 0) {
      // 扣减：按到期先后顺序扣各馆课时包
      let remain = -amount
      const sorted = packages
        .filter((p) => (p.usedHours || 0) < (p.totalHours || 0))
        .sort((a, b) => (a.expireAt || Infinity) - (b.expireAt || Infinity))
      for (const p of sorted) {
        if (remain <= 0) break
        const avail = (p.totalHours || 0) - (p.usedHours || 0)
        const take = Math.min(avail, remain)
        p.usedHours = (p.usedHours || 0) + take
        remain -= take
      }
      // 账本不够扣时静默通过（全局 balance 已校验过不会为负；差额是历史无归属课时）
    }

    await transaction
      .collection('users')
      .doc(user._id)
      .update({ data: { balance: newBalance, packages, updateTime: now } })

    await transaction.collection('hourslog').add({
      data: {
        openid,
        nickName: user.nickName || '',
        type: amount > 0 ? 'add' : 'deduct',
        amount,
        balanceAfter: newBalance,
        reason,
        gymId,
        operatorOpenid: adminOpenid,
        createTime: now,
        dateKey: todayKey(now)
      }
    })

    await transaction.commit()
    // gymId 写入成功与否直接可见，方便排查
    return ok({ balance: newBalance, gymId: gymId || '', gymWritten: !!(amount > 0 && gymId) })
  } catch (e) {
    await transaction.rollback()
    throw e
  }
}

/* ==================== 为会员开通课时套餐 ==================== */
async function recharge(event, adminOpenid) {
  const openid = String(event.openid || '')
  if (!event.packageId) throw bizErr('请选择课时套餐')

  const now = Date.now()
  const pkgRes = await db.collection('package').doc(event.packageId).get()
  const pkg = pkgRes.data
  if (!pkg) throw bizErr('套餐不存在或已删除')

  // 服务端自动解析归属馆：套餐本身有 gymId 用套餐的（该馆定义的套餐）；
  // 否则操作者是馆主 → 名下场馆；超管 → 不归属
  let gymId = pkg.gymId || ''
  if (!gymId) {
    const adminRes = await db.collection('users').where({ openid: adminOpenid }).get()
    const adminUser = adminRes.data[0]
    if (adminUser && adminUser.role === 'owner') {
      const gymRes = await db.collection('gyms').where({ ownerOpenid: adminOpenid }).limit(1).get()
      if (gymRes.data && gymRes.data.length) gymId = gymRes.data[0]._id
    }
  }

  const transaction = await db.startTransaction()
  try {
    const userRes = await transaction.collection('users').where({ openid }).get()
    const user = userRes.data[0]
    if (!user) throw bizErr('会员不存在')

    // 套餐有效期：validDays 天，0 表示长期有效
    const expireAt = pkg.validDays > 0 ? now + pkg.validDays * 86400000 : 0
    const entry = {
      gymId, // 归属馆（按馆隔离展示/扣减）
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
        gymId,
        operatorOpenid: adminOpenid,
        createTime: now,
        dateKey: todayKey(now)
      }
    })

    await transaction.commit()
    return ok({ balance: newBalance, gymId: gymId || '', gymWritten: !!gymId })
  } catch (e) {
    await transaction.rollback()
    throw e
  }
}

/* ==================== 课时清零（数据修正用） ==================== */
async function resetHours(event, adminOpenid) {
  const openid = String(event.openid || '')
  if (!openid) throw bizErr('缺少会员 openid')

  const now = Date.now()
  const transaction = await db.startTransaction()
  try {
    const userRes = await transaction.collection('users').where({ openid }).get()
    const user = userRes.data[0]
    if (!user) throw bizErr('会员不存在')

    const before = {
      balance: user.balance || 0,
      packages: (user.packages || []).map((p) => ({
        gymId: p.gymId || '',
        name: p.name || '',
        remain: Math.max(0, (p.totalHours || 0) - (p.usedHours || 0))
      }))
    }

    // 全局余额清零 + 清空所有课时包（馆账本一并清除）
    await transaction
      .collection('users')
      .doc(user._id)
      .update({ data: { balance: 0, packages: [], updateTime: now } })

    await transaction.collection('hourslog').add({
      data: {
        openid,
        nickName: user.nickName || '',
        type: 'reset',
        amount: -before.balance,
        balanceAfter: 0,
        reason: String(event.reason || '').trim().slice(0, 50) || '数据修正：课时清零',
        operatorOpenid: adminOpenid,
        createTime: now,
        dateKey: todayKey(now)
      }
    })

    await transaction.commit()
    return ok({ before, after: { balance: 0, packages: [] } })
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
      case 'resetHours':
        await assertAdmin(OPENID)
        return await resetHours(event, OPENID)
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
