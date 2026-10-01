/**
 * 云函数：package  课时套餐
 * ------------------------------------------------------------------
 * action:
 *   list    套餐列表（所有人可见，用于展示与选购）
 *   create  新增套餐（管理员）
 *   update  编辑套餐（管理员）
 *   remove  删除套餐（管理员）
 * ------------------------------------------------------------------
 * 说明：会员「已购套餐」存在 users.packages 数组里，
 * 由 member 云函数的 recharge 动作写入。
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

/** 校验并组装套餐字段 */
function buildPackageData(event) {
  const name = String(event.name || '').trim()
  const totalHours = parseInt(event.totalHours, 10)
  const price = Number(event.price)
  const validDays = parseInt(event.validDays, 10) || 0
  const desc = String(event.desc || '').trim().slice(0, 200)

  if (!name) throw bizErr('请填写套餐名称')
  if (!totalHours || totalHours < 1 || totalHours > 999) throw bizErr('总课时需为 1-999')
  if (isNaN(price) || price < 0) throw bizErr('请填写正确的价格')
  if (validDays < 0) throw bizErr('有效期不能为负数（0 = 长期有效）')

  return { name, totalHours, price, validDays, desc, updateTime: Date.now() }
}

async function list() {
  const res = await db.collection('package').orderBy('price', 'asc').limit(50).get()
  return ok({ list: res.data })
}

async function create(event) {
  const data = buildPackageData(event)
  data.createTime = Date.now()
  const res = await db.collection('package').add({ data })
  return ok({ _id: res._id })
}

async function update(event) {
  if (!event.id) throw bizErr('缺少套餐 ID')
  await db.collection('package').doc(event.id).update({ data: buildPackageData(event) })
  return ok({})
}

async function remove(event) {
  if (!event.id) throw bizErr('缺少套餐 ID')
  await db.collection('package').doc(event.id).remove()
  return ok({})
}

exports.main = async (event) => {
  const { OPENID } = cloud.getWXContext()
  const action = event.action || 'list'

  try {
    switch (action) {
      case 'list':
        return await list()
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
    console.error('[package] 执行出错', err)
    return fail(err.isBiz ? err.message : '服务异常，请稍后重试')
  }
}
