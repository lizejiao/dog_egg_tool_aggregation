// pages/invite/owner.js
/**
 * 馆主邀请落地页：展示馆信息 → 接受邀请成为馆主
 */
const app = getApp()
const api = require('../../utils/api')
const auth = require('../../utils/auth')

Page({
  data: {
    inviteId: '',
    loading: true,
    invite: null, // { gymId, gymName, status, mine, expireAt }
    accepting: false
  },

  onLoad(options) {
    this.setData({ inviteId: options.inviteId || '' })
    this.load()
  },

  async load() {
    if (!this.data.inviteId) {
      this.setData({ loading: false })
      return
    }
    try {
      await app.ensureLogin().catch(() => {}) // 游客点开则先登录
      const res = await api.call('gym', { action: 'inviteInfo', inviteId: this.data.inviteId })
      this.setData({ invite: res, loading: false })
      if (res && res.gymName) {
        wx.setNavigationBarTitle({ title: '馆主邀请' })
      }
    } catch (e) {
      this.setData({ loading: false })
      api.fail(e, '邀请信息加载失败')
    }
  },

  /** 接受邀请 */
  async onAccept() {
    const { inviteId, accepting } = this.data
    if (accepting) return

    // 未登录：引导完成登录（弹窗确认 → 静默登录）
    if (!(await auth.ensureLogin('接受馆主邀请需要先登录'))) return

    this.setData({ accepting: true })
    try {
      const res = await api.call('gym', { action: 'acceptInvite', inviteId }, { loading: true, loadingText: '接受中' })
      await app.refreshUser()
      api.alert(
        '你已成为「' + res.gymName + '」的馆主\n\n现在可以完善场馆信息、创建课程了',
        '🎉 恭喜上任'
      ).then(() => {
        wx.switchTab({ url: '/pages/index/index' })
      })
    } catch (e) {
      api.fail(e)
    } finally {
      this.setData({ accepting: false })
    }
  },

  /** 回首页看看 */
  onBack() {
    wx.switchTab({ url: '/pages/index/index' })
  }
})
