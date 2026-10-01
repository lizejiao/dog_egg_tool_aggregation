/**
 * 云函数：login
 * ------------------------------------------------------------------
 * 职责：静默登录（openid 识别会员）、资料完善、手机号绑定、管理员口令绑定
 * 返回结构：{ code, data, message }
 * ------------------------------------------------------------------
 */
const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()

/* ==================== 管理员配置（改这里） ==================== */
// 方式一：把馆主的 openid 写进数组，该用户登录后自动成为管理员
const ADMIN_OPENIDS = []

// 方式二：管理员口令。学员在「我的 → 馆主入口」输入正确口令后升级为管理员
const ADMIN_PASSWORD = 'yoga2026'
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
      // 在 ADMIN_OPENIDS 名单里的 openid 自动授予管理员
      role: ADMIN_OPENIDS.indexOf(openid) > -1 ? 'admin' : 'student',
      balance: 0, // 剩余课时
      packages: [], // 已购课时套餐 [{packageId,name,totalHours,usedHours,price,expireAt,boughtAt}]
      createTime: now,
      updateTime: now
    }
    const added = await db.collection('users').add({ data: doc })
    user = Object.assign({ _id: added._id }, doc)
  }
  return user
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
      const user = await getOrCreateUser(OPENID)
      return ok({ user: safeUser(user) })
    }

    /* ---------- 更新昵称 / 头像 ---------- */
    if (action === 'updateProfile') {
      const nickName = String(event.nickName || '').trim().slice(0, 20)
      const avatarUrl = String(event.avatarUrl || '').trim().slice(0, 500)
      if (!nickName && !avatarUrl) return fail('请填写昵称或头像')
      await db
        .collection('users')
        .where({ openid: OPENID })
        .update({ data: { nickName, avatarUrl, updateTime: Date.now() } })
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
      return ok({ phone })
    }

    /* ---------- 手动填写手机号 ---------- */
    if (action === 'setPhone') {
      const phone = String(event.phone || '').replace(/\D/g, '')
      if (!/^1\d{10}$/.test(phone)) return fail('请输入正确的 11 位手机号')
      await db
        .collection('users')
        .where({ openid: OPENID })
        .update({ data: { phone, updateTime: Date.now() } })
      return ok({ phone })
    }

    /* ---------- 管理员口令绑定：口令正确则把当前用户升级为管理员 ---------- */
    if (action === 'bindAdmin') {
      const password = String(event.password || '').trim()
      if (!password) return fail('请输入管理员口令')
      if (password !== ADMIN_PASSWORD) return fail('口令不正确')

      const user = await getOrCreateUser(OPENID)
      await db
        .collection('users')
        .doc(user._id)
        .update({ data: { role: 'admin', updateTime: Date.now() } })
      return ok({})
    }

    return fail('未知操作：' + action, 404)
  } catch (err) {
    console.error('[login] 执行出错', err)
    return fail(err.isBiz ? err.message : '服务异常，请稍后重试')
  }
}
