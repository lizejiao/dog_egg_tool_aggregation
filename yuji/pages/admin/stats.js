// pages/admin/stats.js
/**
 * 课时统计：概览 + 近 30 天趋势 + 会员消课排行
 */
const api = require('../../../utils/api')
const guard = require('../../../utils/admin')
const fmt = require('../../../utils/format')

Page({
  data: {
    overview: { members: 0, bookedOrders: 0, signedOrders: 0, totalRecharge: 0 },
    daily: [], // 近 30 天消课
    perMember: [] // 会员消课排行
  },

  async onLoad() {
    const ok = await guard()
    if (!ok) return
    this._loaded = true
    this.load()
  },

  onShow() {
    // 从会员详情返回时刷新
    if (this._loaded) this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    try {
      const data = await api.call('member', { action: 'stats' })

      // 趋势柱状图：按最大值换算高度百分比
      const daily = (data.daily || []).map((d) => {
        const date = new Date(String(d._id).replace(/-/g, '/'))
        return {
          _id: d._id,
          count: d.count,
          label: `${date.getMonth() + 1}/${date.getDate()}`
        }
      })
      const max = daily.reduce((m, d) => Math.max(m, d.count), 0) || 1
      daily.forEach((d) => {
        d.percent = Math.max(6, Math.round((d.count / max) * 100))
      })

      this.setData({ overview: data.overview, daily, perMember: data.perMember || [] })
    } catch (e) {
      api.fail(e, '统计数据加载失败')
    }
  },

  /** 点击会员 → 查看消课明细 */
  onMemberTap(e) {
    wx.navigateTo({
      url: '/pages/admin/member/detail?openid=' + e.currentTarget.dataset.openid
    })
  }
})
