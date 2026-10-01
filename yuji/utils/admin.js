/**
 * 管理员页面守卫
 * 所有管理后台页面 onLoad 时调用 guard()，
 * 非管理员会被提示并退出页面。
 */
const api = require('./api')

async function guard() {
  const app = getApp()
  try {
    await app.ensureLogin()
  } catch (e) {
    /* 登录失败也按无权限处理 */
  }

  if (app.isAdmin()) return true

  api.toast('无权限访问，仅管理员可用')
  setTimeout(() => {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: '/pages/index/index' })
    })
  }, 900)
  return false
}

module.exports = { guard }
