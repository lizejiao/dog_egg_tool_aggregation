// pages/admin/home.js
/**
 * 管理后台首页：概览数据 + 功能入口
 */
const api = require('../../utils/api')
const guard = require('../../utils/admin')
const config = require('../../utils/config')

Page({
  data: {
    ready: false,
    shopName: config.SHOP_NAME,
    overview: {
      members: 0,
      bookedOrders: 0,
      signedOrders: 0,
      totalRecharge: 0
    }
  },

  async onLoad() {
    if (!(await guard())) return
    this.setData({ ready: true })
    this.loadOverview()
  },

  async loadOverview() {
    try {
      const data = await api.call('member', { action: 'stats' })
      this.setData({ overview: data.overview })
    } catch (e) {
      api.fail(e, '概览数据加载失败')
    }
  },

  go(e) {
    wx.navigateTo({ url: e.currentTarget.dataset.url })
  }
})
