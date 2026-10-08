// pages/mine/mine.js
/**
 * 我的：会员资料（头像/昵称/手机号）、剩余课时、功能入口、馆主入口
 */
const app = getApp()
const api = require('../../utils/api')
const auth = require('../../utils/auth')

// 微信官方默认灰色头像
const DEFAULT_AVATAR =
  'https://mmbiz.qpic.cn/mmbiz/icTdbqWNOwNRna42FI242Lcia07jQodd2FJGIYQfG0LAJGFxM4FbnQP6yfMxBgJ0F3YRqJCJ1aPAK2dQagdusBZg/0'

Page({
  data: {
    user: null,
    isAdmin: false,
    isSuperAdmin: false,
    isOwner: false,
    myGyms: [],
    roleText: '学员',
    avatarUrl: DEFAULT_AVATAR,
    nickName: '',
    phoneText: '点击绑定手机号',
    activePackages: [], // 使用中的套餐
    bookedCount: 0,
    signinCount: 0
  },

  onShow() {
    // 已退出状态下不自动登录，直到用户点击「登录」
    if (this._loggedOut) return
    this.load()
  },

  /* ---------- 退出后重新登录 ---------- */
  async relogin() {
    try {
      const user = await app.relogin()
      this.applyUser(user)
      this.load()
    } catch (e) {
      api.fail(e, '登录失败')
    }
  },

  /** 游客点功能：引导登录后继续（返回 true 才继续跳转） */
  async _needLogin(tip) {
    if (app.isLoggedIn()) return true
    const ok = await auth.ensureLogin(tip)
    if (ok) {
      // 登录成功：刷新整个页面数据
      this.applyUser(await app.ensureLogin())
      this.load()
    }
    return ok
  },

  /** 拉取会员信息与统计数字 */
  async load() {
    try {
      const user = await app.ensureLogin()
      this.applyUser(user)

      const [ordersRes, signinsRes] = await Promise.all([
        api.call('booking', { action: 'listMine', limit: 100 }),
        api.call('booking', { action: 'mySignins', limit: 100 })
      ])

      const orders = ordersRes.list || []
      this.setData({
        bookedCount: orders.filter((o) => o.status === 'booked').length,
        signinCount: (signinsRes.list || []).length
      })
    } catch (e) {
      api.fail(e, '信息加载失败')
    }
  },

  /** 把用户记录铺到页面上 */
  applyUser(user) {
    const now = Date.now()
    const activePackages = (user.packages || []).filter(
      (p) => (p.usedHours || 0) < p.totalHours && (!p.expireAt || p.expireAt > now)
    )
    const role = user.role || 'student'
    const isSuperAdmin = role === 'superadmin'
    const isOwner = role === 'owner' || role === 'admin'
    this.setData({
      user,
      isAdmin: isOwner || isSuperAdmin,
      isSuperAdmin,
      isOwner,
      myGyms: app.getMyGyms ? app.getMyGyms() : [],
      roleText: isSuperAdmin ? '超级管理员' : isOwner ? '馆主' : '学员',
      avatarUrl: user.avatarUrl || DEFAULT_AVATAR,
      nickName: user.nickName || '',
      phoneText: user.phone ? user.phone : '点击绑定手机号',
      activePackages
    })
  },

  /* ---------- 头像：微信一键选择 → 上传云存储 ---------- */
  async onChooseAvatar(e) {
    const filePath = e.detail && e.detail.avatarUrl
    if (!filePath) return
    wx.showLoading({ title: '保存中', mask: true })
    try {
      const ext = (filePath.match(/\.[a-zA-Z0-9]+$/) || ['.png'])[0]
      const cloudPath = `avatar/${app.globalData.openid || 'user'}_${Date.now()}${ext}`
      const uploadRes = await wx.cloud.uploadFile({ cloudPath, filePath })
      await api.call('login', { action: 'updateProfile', avatarUrl: uploadRes.fileID })
      this.applyUser(await app.refreshUser())
    } catch (err) {
      api.fail(err, '头像保存失败')
    } finally {
      wx.hideLoading()
    }
  },

  /* ---------- 昵称：输入框失焦保存 ---------- */
  async onNicknameBlur(e) {
    const value = (e.detail.value || '').trim()
    if (!value || value === this.data.nickName) return
    try {
      await api.call('login', { action: 'updateProfile', nickName: value })
      this.applyUser(await app.refreshUser())
      api.success('昵称已保存')
    } catch (err) {
      api.fail(err)
    }
  },

  /* ---------- 手机号 ---------- */
  async onGetPhoneNumber(e) {
    // 用户点了「拒绝」或当前小程序不支持一键获取（个人主体）
    if (!e.detail || !e.detail.cloudID) {
      this.manualBindPhone()
      return
    }
    try {
      await api.call('login', { action: 'bindPhone', cloudID: e.detail.cloudID }, { loading: true })
      this.applyUser(await app.refreshUser())
      api.success('手机号已绑定')
    } catch (err) {
      api.fail(err)
    }
  },

  /** 手动输入手机号（降级方案） */
  async manualBindPhone() {
    const value = await api.prompt('绑定手机号', '请输入 11 位手机号', '')
    if (!value) return
    try {
      await api.call('login', { action: 'setPhone', phone: value }, { loading: true })
      this.applyUser(await app.refreshUser())
      api.success('手机号已绑定')
    } catch (err) {
      api.fail(err)
    }
  },

  /* ---------- 首次部署：初始化集合与演示数据 ---------- */
  async onInitDatabase() {
    const confirmed = await api.confirm(
      '将创建数据库集合并写入演示套餐、课程与公告，已存在的数据不受影响。继续吗？',
      '初始化数据'
    )
    if (!confirmed) return
    try {
      const res = await api.call('init', { action: 'run' }, { loading: true, loadingText: '初始化中' })
      const s = res.seeded || {}
      api.alert(
        `初始化完成\n套餐 ${s.packages || 0} 个\n课程 ${s.courses || 0} 节\n公告 ${s.notices || 0} 条`,
        '完成'
      )
    } catch (err) {
      api.fail(err)
    }
  },

  /* ---------- 页面跳转（游客需先登录） ---------- */
  async goOrders() {
    if (!(await this._needLogin('查看我的预约需要先登录'))) return
    wx.navigateTo({ url: '/pages/mine/orders' })
  },
  async goPackages() {
    if (!(await this._needLogin('查看课时套餐需要先登录'))) return
    wx.navigateTo({ url: '/pages/mine/packages' })
  },
  async goSignins() {
    if (!(await this._needLogin('查看签到记录需要先登录'))) return
    wx.navigateTo({ url: '/pages/mine/signins' })
  },
  goAdmin() {
    wx.navigateTo({ url: '/pages/admin/home' })
  },

  /* ---------- 退出登录：点击身份标签 ---------- */
  async onLogout() {
    const confirmed = await api.confirm('确定退出当前账号吗？', '退出登录', '退出')
    if (!confirmed) return
    app.logout()
    this._loggedOut = true
    // 重置页面为未登录态
    this.setData({
      user: null,
      isAdmin: false,
      isSuperAdmin: false,
      isOwner: false,
      myGyms: [],
      roleText: '未登录',
      avatarUrl: DEFAULT_AVATAR,
      nickName: '',
      phoneText: '点击绑定手机号',
      activePackages: [],
      bookedCount: 0,
      signinCount: 0
    })
    api.success('已退出')
  }
})
