// pages/mine/orders.js
/**
 * 我的预约记录：按状态筛选 + 取消预约
 */
const app = getApp()
const api = require('../../utils/api')
const fmt = require('../../utils/format')
const C = require('../../utils/constants')

// tab 与预约状态的对应关系
const TAB_STATUS = ['', 'booked', 'signed', 'canceled']

Page({
  data: {
    tabs: ['全部', '已预约', '已签到', '已取消'],
    current: 0,
    list: [],
    loading: true
  },

  onShow() {
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  onTabChange(e) {
    const current = Number(e.currentTarget.dataset.index)
    this.setData({ current })
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    const status = TAB_STATUS[this.data.current]
    try {
      const data = await api.call('booking', { action: 'listMine', status: status || undefined, limit: 100 })
      const list = (data.list || []).map((o) =>
        Object.assign({}, o, {
          timeText: `${fmt.formatDate(o.startTime)} ${fmt.weekday(o.startTime)} ${fmt.formatTimeRange(
            o.startTime,
            o.endTime
          )}`,
          statusText: C.ORDER_STATUS_TEXT[o.status] || o.status,
          statusClass: C.ORDER_STATUS_CLASS[o.status] || 'tag-gray',
          statusText2: this.extraStatusText(o)
        })
      )
      this.setData({ list })
    } catch (e) {
      api.fail(e, '预约记录加载失败')
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 附加说明：取消时间 / 签到时间 */
  extraStatusText(o) {
    if (o.status === 'canceled' && o.cancelTime) {
      return `已于 ${fmt.formatDateTime(o.cancelTime)} 取消`
    }
    if (o.status === 'signed' && o.signinTime) {
      return `已消课 1 课时 · 签到于 ${fmt.formatDateTime(o.signinTime)}`
    }
    return ''
  },

  /** 取消预约 */
  async onCancel(e) {
    const id = e.currentTarget.dataset.id
    const confirmed = await api.confirm('取消后将释放名额，确定取消这条预约吗？', '取消预约')
    if (!confirmed) return
    try {
      await api.call('booking', { action: 'cancel', orderId: id }, { loading: true, loadingText: '取消中' })
      await app.refreshUser()
      api.success('已取消')
      this.load()
    } catch (err) {
      api.fail(err)
    }
  }
})
