/**
 * 云函数：login
 * ------------------------------------------------------------------
 * 职责：静默登录（openid 识别会员）、资料完善、手机号绑定、
 *       超级管理员手机号自动识别、登录返回名下场馆（myGyms）
 * 返回结构：{ code, data, message }
 * ------------------------------------------------------------------
 */
const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()

/* ==================== 超级管理员配置（改这里） ==================== */
// 手机号命中名单 → 自动升级为 superadmin（登录时 & 绑定手机号后判定）
const SUPER_ADMIN_PHONES = ['17611028882']
// openid 兜底名单（可选）：防止他人手动输入超管手机号冒充。
// 用真机打开小程序登录一次 → 云开发控制台 users 集合复制自己的 openid 填入。
// 留空数组 = 不启用 openid 兜底。
const SUPER_ADMIN_OPENIDS = []
/* ============================================================= */

const ok = (data) => ({ code: 0, data, message: 'ok' })
const fail = (message, code = 1) => ({ code, data: null, message })

/** 业务异常：message 可直接展示给用户 */
function bizErr(message, code = 1) {
  const err = new Error(message)
  err.isBiz = true
  err.code = code
  return err
}

/** 是否命中超管规则 */
function isSuperAdmin(user) {
  if (SUPER_ADMIN_OPENIDS.indexOf(user.openid) > -1) return true
  if (user.phone && SUPER_ADMIN_PHONES.indexOf(user.phone) > -1) return true
  return false
}

/** 查询用户，不存在则创建（首次进入小程序） */
async function getOrCreateUser(openid) {
  const res = await db.collection('users').where({ openid }).get()
  let user = res.data[0]
  if (!user) {
    const now = Date.now()
    const doc = {
      openid,
      nickName: '',
      avatarUrl: '',
      phone: '',
      role: 'student',
      balance: 0, // 剩余课时（全部馆合计，展示用；权威在各 packages 明细）
      packages: [], // 已购课时套餐 [{gymId,packageId,name,totalHours,usedHours,price,expireAt,boughtAt}]
      createTime: now,
      updateTime: now
    }
    // openid 白名单兜底：首次建档即超管
    if (SUPER_ADMIN_OPENIDS.indexOf(openid) > -1) doc.role = 'superadmin'
    const added = await db.collection('users').add({ data: doc })
    user = Object.assign({ _id: added._id }, doc)
  }
  return user
}

/** 手机号绑定后 / 登录时的超管身份同步（升或降都处理） */
async function syncSuperAdminRole(user) {
  const should = isSuperAdmin(user)
  const isNow = user.role === 'superadmin'
  if (should && !isNow) {
    // 升级为超管（原是馆主则同时摘除其名下场馆的 ownerOpenid）
    if (user.role === 'owner') await detachOwnedGyms(user.openid)
    await db
      .collection('users')
      .doc(user._id)
      .update({ data: { role: 'superadmin', updateTime: Date.now() } })
    user.role = 'superadmin'
  } else if (!should && isNow) {
    // 手机号解绑等场景导致不再命中 → 降回学员
    await db
      .collection('users')
      .doc(user._id)
      .update({ data: { role: 'student', updateTime: Date.now() } })
    user.role = 'student'
  }
  return user
}

/** 摘除名下场馆（升级超管时用） */
async function detachOwnedGyms(openid) {
  await db
    .collection('gyms')
    .where({ ownerOpenid: openid })
    .update({ data: { ownerOpenid: '', updateTime: Date.now() } })
}

/** 名下场馆列表 */
async function getMyGyms(openid) {
  const res = await db
    .collection('gyms')
    .where({ ownerOpenid: openid })
    .orderBy('createTime', 'desc')
    .get()
  return res.data.map((g) => ({ _id: g._id, name: g.name, city: g.city, status: g.status }))
}

/** 掩码显示手机号 */
function maskPhone(phone) {
  if (!phone || phone.length < 7) return phone || ''
  return phone.replace(/^(\d{3})\d+(\d{4})$/, '$1****$2')
}

/** 脱敏后的用户信息（返回给前端） */
function safeUser(user) {
  return Object.assign({}, user, {
    phone: maskPhone(user.phone)
  })
}

exports.main = async (event) => {
  const { OPENID } = cloud.getWXContext()
  const action = event.action || 'login'

  try {
    /* ---------- 静默登录：拿 openid，没有则建档案 ---------- */
    if (action === 'login') {
      let user = await getOrCreateUser(OPENID)
      user = await syncSuperAdminRole(user)
      const myGyms = user.role === 'owner' ? await getMyGyms(OPENID) : []
      return ok({ user: safeUser(user), myGyms })
    }

    /* ---------- 更新昵称 / 头像（只更新传入的字段，避免互相覆盖） ---------- */
    if (action === 'updateProfile') {
      const data = { updateTime: Date.now() }
      if (event.nickName !== undefined) {
        const nickName = String(event.nickName || '').trim().slice(0, 20)
        if (!nickName) return fail('昵称不能为空')
        data.nickName = nickName
      }
      if (event.avatarUrl !== undefined) {
        const avatarUrl = String(event.avatarUrl || '').trim().slice(0, 500)
        if (!avatarUrl) return fail('头像不能为空')
        data.avatarUrl = avatarUrl
      }
      if (Object.keys(data).length === 1) return fail('请填写昵称或头像')
      await db
        .collection('users')
        .where({ openid: OPENID })
        .update({ data })
      return ok({})
    }

    /* ---------- 绑定手机号（微信一键获取，cloudID 解密） ---------- */
    if (action === 'bindPhone') {
      if (!event.cloudID) return fail('未获取到手机号授权')
      const res = await cloud.getOpenData({ list: [event.cloudID] })
      const info = res.list && res.list[0] && res.list[0].data
      const phone = info && (info.purePhoneNumber || info.phoneNumber)
      if (!phone) return fail('手机号获取失败，请手动填写')
      await db
        .collection('users')
        .where({ openid: OPENID })
        .update({ data: { phone, updateTime: Date.now() } })
      // 绑定手机号后重新判定超管身份
      let user = await getOrCreateUser(OPENID)
      user.phone = phone
      user = await syncSuperAdminRole(user)
      const myGyms = user.role === 'owner' ? await getMyGyms(OPENID) : []
      return ok({ phone, role: user.role, myGyms })
    }

    /* ---------- 手动填写手机号 ---------- */
    if (action === 'setPhone') {
      const phone = String(event.phone || '').replace(/\D/g, '')
      if (!/^1\d{10}$/.test(phone)) return fail('请输入正确的 11 位手机号')
      await db
        .collection('users')
        .where({ openid: OPENID })
        .update({ data: { phone, updateTime: Date.now() } })
      // 绑定手机号后重新判定超管身份
      let user = await getOrCreateUser(OPENID)
      user.phone = phone
      user = await syncSuperAdminRole(user)
      const myGyms = user.role === 'owner' ? await getMyGyms(OPENID) : []
      return ok({ phone, role: user.role, myGyms })
    }

    return fail('未知操作：' + action, 404)
  } catch (err) {
    console.error('[login] 执行出错', err)
    return fail(err.isBiz ? err.message : '服务异常，请稍后重试')
  }
}
