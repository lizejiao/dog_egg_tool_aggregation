// pages/mine/signins.js
/**
 * 历史签到记录
 */
const app = getApp()
const api = require('../../utils/api')
const fmt = require('../../utils/format')

Page({
  data: {
    list: [],
    loading: true
  },

  onShow() {
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    this.setData({ loading: true })
    try {
      const data = await api.call('booking', { action: 'mySignins', limit: 100 })
      const list = (data.list || []).map((s) =>
        Object.assign({}, s, {
          timeText: `${fmt.formatDate(s.startTime)} ${fmt.weekday(s.startTime)} ${fmt.formatTime(
            s.startTime
          )}`,
          signinText: fmt.formatDateTime(s.signinTime)
        })
      )
      this.setData({ list })
    } catch (e) {
      api.fail(e, '签到记录加载失败')
    } finally {
      this.setData({ loading: false })
    }
  }
})
