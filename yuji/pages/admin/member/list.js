// pages/admin/member/list.js
/**
 * 会员列表：搜索 + 点击进入会员详情（课时调整）
 */
const api = require('../../../utils/api')
const guard = require('../../../utils/admin')

const PAGE_SIZE = 50

Page({
  data: {
    keyword: '',
    list: [],
    total: 0,
    finished: false,
    loading: true
  },

  async onLoad() {
    const ok = await guard()
    if (!ok) return
    this._loaded = true
    this.load()
  },

  onShow() {
    // 从会员详情调整完课时返回时刷新
    if (this._loaded) this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value })
  },

  onSearch() {
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    try {
      const data = await api.call('member', {
        action: 'list',
        keyword: this.data.keyword.trim(),
        limit: PAGE_SIZE
      })
      const list = (data.list || []).map((u) =>
        Object.assign({}, u, {
          avatarText: (u.nickName || '客')[0]
        })
      )
      this.setData({
        list,
        total: data.total || list.length,
        finished: list.length < PAGE_SIZE
      })
    } catch (e) {
      api.fail(e, '会员加载失败')
    } finally {
      this.setData({ loading: false })
    }
  },

  onItemTap(e) {
    wx.navigateTo({
      url: '/pages/admin/member/detail?openid=' + e.currentTarget.dataset.openid
    })
  },

  onGoStats() {
    wx.navigateTo({ url: '/pages/admin/stats' })
  }
})
