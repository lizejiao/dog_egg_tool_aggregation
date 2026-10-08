/**
 * 云函数：gym  场馆管理（多馆平台核心）
 * ------------------------------------------------------------------
 * 角色：superadmin（超级管理员）/ owner（馆主）/ student（学员）
 *
 * action:
 *   list            场馆列表（按城市，学员端用；status=active）
 *   detail          场馆详情
 *   create          创建场馆（超管）
 *   update          编辑场馆（超管；馆主仅能改自己馆的定位信息）
 *   close           关闭/重开场馆（超管）
 *   setLocation     馆主开启/关闭场馆定位，录入坐标（馆主）
 *   searchUsers     搜索注册用户（超管，指定馆主用）
 *   setOwner        指定馆主（超管，即时生效）
 *   revokeOwner     撤销馆主（超管）
 *   invite          生成馆主邀请（超管）
 *   inviteInfo      邀请详情（落地页展示）
 *   acceptInvite    接受邀请成为馆主（被邀请人）
 *   myGyms          我名下的场馆（馆主）
 *   allList         全部场馆（超管，含 closed）
 * ------------------------------------------------------------------
 */
const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()
const _ = db.command

const INVITE_EXPIRE_MS = 7 * 24 * 3600 * 1000 // 邀请有效期 7 天

const ok = (data) => ({ code: 0, data, message: 'ok' })
const fail = (message, code = 1) => ({ code, data: null, message })

function bizErr(message, code = 1) {
  const err = new Error(message)
  err.isBiz = true
  err.code = code
  return err
}

/** 获取用户（不存在返回 null） */
async function getUser(openid) {
  const res = await db.collection('users').where({ openid }).get()
  return res.data[0] || null
}

/** 权限：超级管理员 */
async function assertSuperAdmin(openid) {
  const user = await getUser(openid)
  if (!user || user.role !== 'superadmin') throw bizErr('无权限操作，仅超级管理员可用', 403)
  return user
}

/** 权限：指定场馆的馆主（超管放行） */
async function assertGymOwner(openid, gymId) {
  const user = await getUser(openid)
  if (!user) throw bizErr('用户不存在', 403)
  const gymRes = await db.collection('gyms').doc(gymId).get().catch(() => null)
  const gym = gymRes && gymRes.data
  if (!gym) throw bizErr('场馆不存在', 404)
  if (user.role === 'superadmin') return { user, gym }
  if (gym.ownerOpenid === openid) return { user, gym }
  throw bizErr('无权限操作，仅该馆馆主可用', 403)
}

/** 掩码显示手机号 */
function maskPhone(phone) {
  if (!phone || phone.length < 7) return phone || ''
  return phone.replace(/^(\d{3})\d+(\d{4})$/, '$1****$2')
}

/** 学员端馆卡片 */
function toGymCard(g) {
  return {
    _id: g._id,
    name: g.name,
    city: g.city,
    address: g.address || '',
    phone: g.phone || '',
    desc: g.desc || '',
    images: g.images || [],
    status: g.status || 'active',
    ownerOpenid: g.ownerOpenid || '',
    locateEnabled: !!g.locateEnabled,
    latitude: g.latitude || 0,
    longitude: g.longitude || 0,
    courseCount: g.courseCount || 0,
    createTime: g.createTime
  }
}

exports.main = async (event) => {
  const { OPENID } = cloud.getWXContext()
  const action = event.action || 'list'

  try {
/* ==================== 学员端 ==================== */

/* 场馆列表（按城市，学员端用；status=active；带 isJoined 标记） */
if (action === 'list') {
  const city = String(event.city || '').trim()
  const keyword = String(event.keyword || '').trim()
  const where = { status: 'active' }
  if (city) where.city = city
  if (keyword) where.name = db.RegExp({ regexp: escapeRegexp(keyword), options: 'i' })
  const res = await db
    .collection('gyms')
    .where(where)
    .orderBy('createTime', 'desc')
    .limit(100)
    .get()

  // 已加入集合（登录用户，仅审批通过的）
  let joinedSet = {}
  let pendingSet = {}
  try {
    const jRes = await db.collection('gym_members').where({ openid: OPENID }).get()
    ;(jRes.data || []).forEach((m) => {
      if (m.status === 'approved') joinedSet[m.gymId] = true
      if (m.status === 'pending') pendingSet[m.gymId] = true
    })
  } catch (e) {
    /* 集合可能不存在（未初始化），忽略 */
  }

  return ok({
    list: res.data.map((g) =>
      Object.assign(toGymCard(g), {
        isJoined: !!joinedSet[g._id],
        isPending: !!pendingSet[g._id]
      })
    )
  })
}

/* 加入场馆（登录用户）→ 提交申请，待馆主审批 */
if (action === 'join') {
  const gymId = String(event.gymId || '').trim()
  if (!gymId) return fail('缺少场馆 ID')
  const gymRes = await db.collection('gyms').doc(gymId).get().catch(() => null)
  const gym = gymRes && gymRes.data
  if (!gym || gym.status !== 'active') return fail('场馆不存在或已关闭', 404)
  if (gym.ownerOpenid === OPENID) return fail('你已是该场馆馆主，无需加入')

  const exist = await db.collection('gym_members').where({ openid: OPENID, gymId }).get()
  const rec = exist.data && exist.data[0]

  if (rec && rec.status === 'approved') return ok({}) // 已是成员，幂等
  if (rec && rec.status === 'pending') return ok({}) // 申请审核中，幂等

  const now = Date.now()
  if (rec) {
    // 曾被拒绝/退出过 → 复活记录重新申请
    await db.collection('gym_members').doc(rec._id).update({
      data: { status: 'pending', applyTime: now }
    })
  } else {
    await db.collection('gym_members').add({
      data: { openid: OPENID, gymId, gymName: gym.name, status: 'pending', createTime: now, applyTime: now }
    })
  }
  return ok({})
}

/* 馆主：待审批的加入申请列表 */
if (action === 'pendingList') {
  const gymId = String(event.gymId || '').trim()
  if (!gymId) return fail('缺少场馆 ID')
  await assertGymOwner(OPENID, gymId)

  const res = await db
    .collection('gym_members')
    .where({ gymId, status: 'pending' })
    .orderBy('applyTime', 'desc')
    .limit(50)
    .get()
  const list = res.data || []
  if (!list.length) return ok({ list: [] })

  // 带出申请人资料
  const openids = Array.from(new Set(list.map((m) => m.openid)))
  const usersRes = await db.collection('users').where({ openid: _.in(openids) }).limit(100).get()
  const userMap = {}
  ;(usersRes.data || []).forEach((u) => {
    userMap[u.openid] = u
  })
  return ok({
    list: list.map((m) => ({
      _id: m._id,
      openid: m.openid,
      nickName: (userMap[m.openid] && userMap[m.openid].nickName) || '未设置昵称',
      avatarUrl: (userMap[m.openid] && userMap[m.openid].avatarUrl) || '',
      phone: maskPhone(userMap[m.openid] && userMap[m.openid].phone),
      applyTime: m.applyTime || m.createTime
    }))
  })
}

/* 馆主：同意加入申请 */
if (action === 'approve') {
  const requestId = String(event.requestId || '').trim()
  if (!requestId) return fail('缺少申请 ID')
  const reqRes = await db.collection('gym_members').doc(requestId).get().catch(() => null)
  const req = reqRes && reqRes.data
  if (!req || req.status !== 'pending') return fail('该申请不存在或已处理')

  await assertGymOwner(OPENID, req.gymId)
  await db.collection('gym_members').doc(requestId).update({
    data: { status: 'approved', approveTime: Date.now() }
  })
  return ok({})
}

/* 馆主：拒绝加入申请 */
if (action === 'reject') {
  const requestId = String(event.requestId || '').trim()
  if (!requestId) return fail('缺少申请 ID')
  const reqRes = await db.collection('gym_members').doc(requestId).get().catch(() => null)
  const req = reqRes && reqRes.data
  if (!req || req.status !== 'pending') return fail('该申请不存在或已处理')

  await assertGymOwner(OPENID, req.gymId)
  await db.collection('gym_members').doc(requestId).update({
    data: { status: 'rejected', rejectTime: Date.now() }
  })
  return ok({})
}

/* 退出场馆 / 撤回加入申请（删除自己的 approved 或 pending 记录） */
if (action === 'leave') {
  const gymId = String(event.gymId || '').trim()
  if (!gymId) return fail('缺少场馆 ID')
  await db
    .collection('gym_members')
    .where({ openid: OPENID, gymId, status: _.in(['approved', 'pending']) })
    .remove()
  return ok({})
}

/* 我加入的场馆（已审批通过的） */
if (action === 'myJoined') {
  const res = await db.collection('gym_members').where({ openid: OPENID }).get()
  const members = (res.data || []).filter((m) => m.status === 'approved')
  if (!members.length) return ok({ list: [] })
  const gymIds = members.map((m) => m.gymId)
  const gymsRes = await db
    .collection('gyms')
    .where({ _id: _.in(gymIds) })
    .get()
  const gymMap = {}
  ;(gymsRes.data || []).forEach((g) => {
    gymMap[g._id] = g
  })
  const list = members
    .map((m) => gymMap[m.gymId])
    .filter((g) => g && g.status === 'active')
    .map((g) => Object.assign(toGymCard(g), { isJoined: true }))
  return ok({ list })
}

/* 场馆详情 */
if (action === 'detail') {
  const gymId = String(event.gymId || '').trim()
  if (!gymId) return fail('缺少场馆 ID')
  const res = await db.collection('gyms').doc(gymId).get().catch(() => null)
  const gym = res && res.data
  if (!gym || gym.status !== 'active') return fail('场馆不存在或已关闭', 404)

  // 带出是否已加入 / 申请审核中
  let isJoined = false
  let isPending = false
  try {
    const jRes = await db.collection('gym_members').where({ openid: OPENID, gymId }).get()
    const rec = jRes.data && jRes.data[0]
    if (rec) {
      isJoined = rec.status === 'approved'
      isPending = rec.status === 'pending'
    }
  } catch (e) {
    /* 忽略 */
  }
  return ok({ gym: Object.assign(toGymCard(gym), { isJoined, isPending }) })
}

    /* 我名下的场馆 */
    if (action === 'myGyms') {
      const res = await db
        .collection('gyms')
        .where({ ownerOpenid: OPENID })
        .orderBy('createTime', 'desc')
        .get()
      return ok({ list: res.data.map(toGymCard) })
    }

    /* ==================== 超管：场馆 CRUD ==================== */

    /* 创建场馆 */
    if (action === 'create') {
      await assertSuperAdmin(OPENID)
      const name = String(event.name || '').trim()
      const city = String(event.city || '').trim()
      if (!name) return fail('请填写场馆名称')
      if (!city) return fail('请选择场馆所在城市')
      const now = Date.now()
      const added = await db.collection('gyms').add({
        data: {
          name,
          city,
          address: String(event.address || '').trim(),
          phone: String(event.phone || '').trim(),
          images: [],
          desc: String(event.desc || '').trim(),
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
      return ok({ gymId: added._id })
    }

    /* 编辑场馆（超管可改全部；馆主只能改自己馆的定位信息） */
    if (action === 'update') {
      const gymId = String(event.gymId || '').trim()
      const user = await getUser(OPENID)
      if (!user) return fail('用户不存在', 403)

      const res = await db.collection('gyms').doc(gymId).get().catch(() => null)
      const gym = res && res.data
      if (!gym) return fail('场馆不存在', 404)

      if (user.role === 'superadmin') {
        const data = { updateTime: Date.now() }
        if (event.name !== undefined) data.name = String(event.name).trim()
        if (event.city !== undefined) data.city = String(event.city).trim()
        if (event.address !== undefined) data.address = String(event.address).trim()
        if (event.phone !== undefined) data.phone = String(event.phone).trim()
        if (event.desc !== undefined) data.desc = String(event.desc).trim()
        await db.collection('gyms').doc(gymId).update({ data })
        return ok({})
      }

      if (gym.ownerOpenid === OPENID) {
        // 馆主：只允许改简介与联系电话（定位走 setLocation）
        const data = { updateTime: Date.now() }
        if (event.desc !== undefined) data.desc = String(event.desc).trim()
        if (event.phone !== undefined) data.phone = String(event.phone).trim()
        await db.collection('gyms').doc(gymId).update({ data })
        return ok({})
      }

      return fail('无权限操作', 403)
    }

    /* 关闭/重开场馆（超管） */
    if (action === 'close') {
      await assertSuperAdmin(OPENID)
      const gymId = String(event.gymId || '').trim()
      const reopen = !!event.reopen
      await db
        .collection('gyms')
        .doc(gymId)
        .update({ data: { status: reopen ? 'active' : 'closed', updateTime: Date.now() } })
      return ok({})
    }

    /* 馆主开启/关闭场馆定位（馆主/超管） */
    if (action === 'setLocation') {
      const gymId = String(event.gymId || '').trim()
      await assertGymOwner(OPENID, gymId)
      const enable = !!event.enable
      const data = { locateEnabled: enable, updateTime: Date.now() }
      if (enable) {
        const lat = Number(event.latitude) || 0
        const lng = Number(event.longitude) || 0
        if (!lat || !lng) return fail('请先选择场馆位置')
        data.latitude = lat
        data.longitude = lng
      }
      await db.collection('gyms').doc(gymId).update({ data })
      return ok({})
    }

    /* ==================== 超管：馆主任免 ==================== */

    /* 搜索注册用户（授权馆主用；空关键词返回最近注册的用户） */
    if (action === 'searchUsers') {
      await assertSuperAdmin(OPENID)
      const keyword = String(event.keyword || '').trim()
      let res
      if (!keyword) {
        // 全部注册用户（按注册时间倒序）
        res = await db
          .collection('users')
          .orderBy('createTime', 'desc')
          .limit(50)
          .get()
      } else {
        const reg = db.RegExp({ regexp: escapeRegexp(keyword), options: 'i' })
        res = await db
          .collection('users')
          .where(_.or([{ nickName: reg }, { phone: reg }]))
          .limit(50)
          .get()
      }
      return ok({
        list: res.data.map((u) => ({
          _id: u._id,
          openid: u.openid,
          nickName: u.nickName || '未设置昵称',
          avatarUrl: u.avatarUrl || '',
          phone: maskPhone(u.phone),
          role: u.role || 'student',
          isSuperAdmin: u.role === 'superadmin',
          createTime: u.createTime
        }))
      })
    }

    /* 指定馆主（超管，即时生效） */
    if (action === 'setOwner') {
      await assertSuperAdmin(OPENID)
      const gymId = String(event.gymId || '').trim()
      const ownerOpenid = String(event.ownerOpenid || '').trim()
      if (!gymId || !ownerOpenid) return fail('缺少参数')

      const ownerUser = await getUser(ownerOpenid)
      if (!ownerUser) return fail('目标用户不存在')
      if (ownerUser.role === 'superadmin') return fail('超级管理员不能兼任馆主')

      const gymRes = await db.collection('gyms').doc(gymId).get().catch(() => null)
      const gym = gymRes && gymRes.data
      if (!gym) return fail('场馆不存在', 404)

      const now = Date.now()
      // 1) 若该馆已有馆主，先把旧馆主降级（如其名下无其他馆）
      if (gym.ownerOpenid && gym.ownerOpenid !== ownerOpenid) {
        await downgradeOldOwner(gym.ownerOpenid, gymId)
      }
      // 2) 设置新馆主
      await db
        .collection('gyms')
        .doc(gymId)
        .update({ data: { ownerOpenid, updateTime: now } })
      // 3) 目标用户升级为 owner（保留超管身份不可能，前面已挡）
      await db.collection('users').doc(ownerUser._id).update({
        data: { role: 'owner', updateTime: now }
      })
      return ok({})
    }

    /* 撤销馆主（超管） */
    if (action === 'revokeOwner') {
      await assertSuperAdmin(OPENID)
      const gymId = String(event.gymId || '').trim()
      const gymRes = await db.collection('gyms').doc(gymId).get().catch(() => null)
      const gym = gymRes && gymRes.data
      if (!gym) return fail('场馆不存在', 404)
      if (!gym.ownerOpenid) return fail('该馆当前没有馆主')

      await downgradeOldOwner(gym.ownerOpenid, gymId)
      await db
        .collection('gyms')
        .doc(gymId)
        .update({ data: { ownerOpenid: '', updateTime: Date.now() } })
      return ok({})
    }

    /* ==================== 超管：邀请链路 ==================== */

    /* 生成馆主邀请（超管） */
    if (action === 'invite') {
      await assertSuperAdmin(OPENID)
      const gymId = String(event.gymId || '').trim()
      const gymRes = await db.collection('gyms').doc(gymId).get().catch(() => null)
      const gym = gymRes && gymRes.data
      if (!gym) return fail('场馆不存在', 404)

      const now = Date.now()
      const added = await db.collection('invitations').add({
        data: {
          gymId,
          gymName: gym.name,
          invitedBy: OPENID,
          status: 'pending',
          expireAt: now + INVITE_EXPIRE_MS,
          createTime: now,
          acceptTime: 0,
          acceptOpenid: ''
        }
      })
      return ok({ inviteId: added._id, gymName: gym.name, expireAt: now + INVITE_EXPIRE_MS })
    }

    /* 邀请详情（落地页展示，无需权限） */
    if (action === 'inviteInfo') {
      const inviteId = String(event.inviteId || '').trim()
      if (!inviteId) return fail('缺少邀请 ID')
      const res = await db.collection('invitations').doc(inviteId).get().catch(() => null)
      const inv = res && res.data
      if (!inv) return fail('邀请不存在或已删除', 404)

      let status = inv.status
      if (status === 'pending' && Date.now() > inv.expireAt) status = 'expired'

      // 已被当前用户接受过则直接告知
      let mine = false
      if (status === 'accepted' && inv.acceptOpenid === OPENID) mine = true

      return ok({
        inviteId: inv._id,
        gymId: inv.gymId,
        gymName: inv.gymName,
        status,
        mine,
        expireAt: inv.expireAt
      })
    }

    /* 接受邀请（被邀请人） */
    if (action === 'acceptInvite') {
      const inviteId = String(event.inviteId || '').trim()
      if (!inviteId) return fail('缺少邀请 ID')

      const me = await getUser(OPENID)
      if (!me) return fail('请先登录', 403)
      if (me.role === 'superadmin') return fail('超级管理员不能兼任馆主')

      const res = await db.collection('invitations').doc(inviteId).get().catch(() => null)
      const inv = res && res.data
      if (!inv) return fail('邀请不存在', 404)
      if (inv.status !== 'pending') return fail('该邀请已处理（' + inv.status + '）')
      if (Date.now() > inv.expireAt) return fail('邀请已过期')

      const gymRes = await db.collection('gyms').doc(inv.gymId).get().catch(() => null)
      const gym = gymRes && gymRes.data
      if (!gym) return fail('场馆不存在', 404)

      const now = Date.now()
      // 若馆已有馆主且不是自己，需要先降级旧馆主
      if (gym.ownerOpenid && gym.ownerOpenid !== OPENID) {
        await downgradeOldOwner(gym.ownerOpenid, inv.gymId)
      }
      await db.collection('gyms').doc(inv.gymId).update({
        data: { ownerOpenid: OPENID, updateTime: now }
      })
      await db.collection('users').doc(me._id).update({
        data: { role: 'owner', updateTime: now }
      })
      await db.collection('invitations').doc(inviteId).update({
        data: { status: 'accepted', acceptTime: now, acceptOpenid: OPENID }
      })
      return ok({ gymId: inv.gymId, gymName: inv.gymName })
    }

    /* 全部场馆（超管，含 closed；带馆主昵称） */
    if (action === 'allList') {
      await assertSuperAdmin(OPENID)
      const res = await db.collection('gyms').orderBy('createTime', 'desc').limit(100).get()
      const gyms = res.data || []

      // 批量查馆主昵称
      const ownerOpenids = Array.from(new Set(gyms.filter((g) => g.ownerOpenid).map((g) => g.ownerOpenid)))
      let ownerMap = {}
      if (ownerOpenids.length) {
        const usersRes = await db.collection('users').where({ openid: _.in(ownerOpenids) }).limit(100).get()
        ;(usersRes.data || []).forEach((u) => {
          ownerMap[u.openid] = u.nickName || '未设置昵称'
        })
      }

      return ok({
        list: gyms.map((g) =>
          Object.assign(toGymCard(g), { ownerName: ownerMap[g.ownerOpenid] || '' })
        )
      })
    }

    return fail('未知操作：' + action, 404)
  } catch (err) {
    console.error('[gym] 执行出错', err)
    return fail(err.isBiz ? err.message : '服务异常，请稍后重试')
  }
}

/** 旧馆主降级：若其名下已无其他馆，role 退回 student */
async function downgradeOldOwner(ownerOpenid, excludeGymId) {
  const res = await db
    .collection('gyms')
    .where({ ownerOpenid, _id: _.neq(excludeGymId) })
    .count()
  if (res.total === 0) {
    const u = await getUser(ownerOpenid)
    if (u && u.role === 'owner') {
      await db
        .collection('users')
        .doc(u._id)
        .update({ data: { role: 'student', updateTime: Date.now() } })
    }
  }
}

/** 正则转义 */
function escapeRegexp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
