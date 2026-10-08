/**
 * 登录守卫：功能操作前的统一登录拦截
 * ------------------------------------------------------------------
 * 场景：游客（未登录/已退出）点击「预约 / 申请加入 / 购买套餐」等功能时，
 *      弹窗引导登录，确认后静默完成微信登录并继续原操作。
 *
 * 用法：
 *   const auth = require('../../utils/auth')
 *
 *   async onBook() {
 *     if (!(await auth.ensureLogin('预约课程需要先登录'))) return
 *     // ...登录成功，继续业务
 *   }
 * ------------------------------------------------------------------
 */
const app = getApp()
const api = require('./api')

/**
 * 检查登录态；未登录则弹窗引导并完成登录
 * @param {string} tip 弹窗提示文案（如「预约课程需要先登录」）
 * @returns {Promise<boolean>} true=已登录（可能是刚登录上的）；false=用户拒绝或登录失败
 */
function ensureLogin(tip = '该功能需要登录后使用') {
  if (app.isLoggedIn()) return Promise.resolve(true)

  return api
    .confirm(tip + '\n\n将使用微信身份快捷登录，是否继续？', '微信授权登录', '立即登录')
    .then((confirmed) => {
      if (!confirmed) return false
      return doLogin()
    })
    .catch(() => false)
}

/** 执行登录（清除退出标记 → 静默登录） */
function doLogin() {
  return app
    .relogin()
    .then(() => {
      api.success('登录成功')
      return true
    })
    .catch((e) => {
      api.fail(e, '登录失败，请稍后重试')
      return false
    })
}

module.exports = { ensureLogin }
