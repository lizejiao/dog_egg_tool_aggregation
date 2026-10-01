// app.js
/**
 * 小程序入口
 * ------------------------------------------------------------------
 * 职责：
 *   1. 初始化云开发环境
 *   2. 静默登录（拿 openid 识别会员身份，并缓存用户资料到 globalData）
 *   3. 对外提供 ensureLogin() / refreshUser() 给各页面复用
 * ------------------------------------------------------------------
 */
const config = require('./utils/config')
const api = require('./utils/api')

App({
  globalData: {
    openid: '', // 当前用户 openid
    user: null, // 用户记录（含 role / balance / packages）
    isAdmin: false, // 是否管理员
    windowInfo: null // 窗口信息（用于安全区适配）
  },

  onLaunch() {
    // ---------- 1. 云开发初始化 ----------
    if (!wx.cloud) {
      console.error('[云开发] 当前基础库版本过低，请在开发者工具中把调试基础库调整到 2.2.3 以上')
      return
    }
    const initOptions = { traceUser: true }
    if (config.CLOUD_ENV) {
      // 填写了环境 ID 则使用指定环境，否则使用默认环境
      initOptions.env = config.CLOUD_ENV
    }
    wx.cloud.init(initOptions)

    // ---------- ★ 临时：首次启动自动初始化数据库（跑通后可删除这段） ----------
    if (!wx.getStorageSync('_dbInited')) {
      wx.cloud
        .callFunction({ name: 'init', data: { action: 'run' } })
        .then((res) => {
          console.log('[init] 数据库初始化完成：', res && res.result)
          wx.setStorageSync('_dbInited', true)
        })
        .catch((err) => {
          console.error('[init] 数据库初始化失败（重进小程序会重试）：', err)
        })
    }
    // ---------- ★ 临时代码结束 ----------

    // ---------- 2. 窗口信息 ----------
    try {
      this.globalData.windowInfo = (wx.getWindowInfo && wx.getWindowInfo()) || wx.getSystemInfoSync()
    } catch (e) {
      this.globalData.windowInfo = null
    }

    // ---------- 3. 静默登录（失败不阻塞启动，页面里会再试一次） ----------
    this.ensureLogin().catch((err) => {
      console.warn('[登录] 首次静默登录失败：', err && err.message)
    })
  },

  /**
   * 登录 / 刷新用户资料（带缓存，页面里可以随便调）
   * @param {boolean} force 是否强制重新拉取
   * @returns {Promise<object>} 用户记录
   */
  login(force) {
    if (!force && this._loginPromise) return this._loginPromise

    this._loginPromise = api.call('login', { action: 'login' }).then((data) => {
      const user = data.user || {}
      this.globalData.openid = user.openid || ''
      this.globalData.user = user
      this.globalData.isAdmin = user.role === 'admin'
      return user
    })

    this._loginPromise.catch(() => {
      // 失败时清掉缓存，下次调用可以重试
      this._loginPromise = null
    })

    return this._loginPromise
  },

  /** 页面里等待登录完成的统一入口 */
  ensureLogin() {
    if (this.globalData.user) return Promise.resolve(this.globalData.user)
    return this.login()
  },

  /** 强制刷新用户资料（预约 / 签到 / 充值后调用，同步剩余课时） */
  refreshUser() {
    return this.login(true)
  },

  /** 本地退出（仅清理缓存，下次进入会重新静默登录） */
  logout() {
    this._loginPromise = null
    this.globalData.openid = ''
    this.globalData.user = null
    this.globalData.isAdmin = false
  },

  /** 当前是否管理员（以服务端 role 为准） */
  isAdmin() {
    return !!(this.globalData.user && this.globalData.user.role === 'admin')
  }
})
