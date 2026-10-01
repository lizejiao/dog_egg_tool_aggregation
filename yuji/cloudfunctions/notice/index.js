/**
 * 云函数：notice  公告
 * ------------------------------------------------------------------
 * action:
 *   list    公告列表（所有人可见）
 *   create  新增公告（管理员）
 *   update  编辑公告（管理员）
 *   remove  删除公告（管理员）
 */
const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()

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

async function list(event) {
  const limit = Math.min(parseInt(event.limit, 10) || 5, 20)
  const res = await db.collection('notice').orderBy('createTime', 'desc').limit(limit).get()
  return ok({ list: res.data })
}

async function create(event, openid) {
  const title = String(event.title || '').trim().slice(0, 50)
  const content = String(event.content || '').trim().slice(0, 500)
  if (!title) throw bizErr('请填写公告标题')
  if (!content) throw bizErr('请填写公告内容')

  await db.collection('notice').add({
    data: { title, content, createTime: Date.now(), updateTime: Date.now(), creatorOpenid: openid }
  })
  return ok({})
}

async function update(event) {
  if (!event.id) throw bizErr('缺少公告 ID')
  const title = String(event.title || '').trim().slice(0, 50)
  const content = String(event.content || '').trim().slice(0, 500)
  if (!title || !content) throw bizErr('请填写完整的公告内容')
  await db.collection('notice').doc(event.id).update({ data: { title, content, updateTime: Date.now() } })
  return ok({})
}

async function remove(event) {
  if (!event.id) throw bizErr('缺少公告 ID')
  await db.collection('notice').doc(event.id).remove()
  return ok({})
}

exports.main = async (event) => {
  const { OPENID } = cloud.getWXContext()
  const action = event.action || 'list'

  try {
    switch (action) {
      case 'list':
        return await list(event)
      case 'create':
        await assertAdmin(OPENID)
        return await create(event, OPENID)
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
    console.error('[notice] 执行出错', err)
    return fail(err.isBiz ? err.message : '服务异常，请稍后重试')
  }
}
