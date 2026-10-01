// pages/admin/order/list.js
/**
 * 预约管理：查看全部预约，执行签到 / 取消
 * 签到会自动扣减会员 1 课时（booking 云函数内处理）
 */
const api = require('../../../utils/api')
const guard = require('../../../utils/admin')
const fmt = require('../../../utils/format')
const C = require('../../../utils/constants')

const TAB_STATUS = ['booked', 'signed', 'canceled', '']

Page({
  data: {
    tabs: ['待签到', '已签到', '已取消', '全部'],
    current: 0,
    list: [],
    loading: true
  },

  async onLoad() {
    const ok = await guard()
    if (!ok) return
    this._loaded = true
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  onTabChange(e) {
    this.setData({ current: Number(e.currentTarget.dataset.index) })
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    const status = TAB_STATUS[this.data.current]
    try {
      const data = await api.call('booking', {
        action: 'listAll',
        status: status || undefined,
        limit: 50
      })

      const list = (data.list || []).map((o) => {
        const member = (data.userMap && data.userMap[o.openid]) || {}
        let text2 = ''
        if (o.status === 'signed' && o.signinTime) text2 = `签到于 ${fmt.formatDateTime(o.signinTime)}`
        if (o.status === 'canceled' && o.cancelTime) {
          text2 = `取消于 ${fmt.formatDateTime(o.cancelTime)}${o.cancelBy === 'admin' ? '（馆主操作）' : ''}`
        }
        return Object.assign({}, o, {
          memberName: member.nickName || '未设置昵称',
          memberPhone: member.phone || '',
          timeText: `${fmt.formatDate(o.startTime)} ${fmt.weekday(o.startTime)} ${fmt.formatTimeRange(
            o.startTime,
            o.endTime
          )}`,
          createText: fmt.formatDateTime(o.createTime),
          statusText: C.ORDER_STATUS_TEXT[o.status] || o.status,
          statusClass: C.ORDER_STATUS_CLASS[o.status] || 'tag-gray',
          statusText2: text2
        })
      })

      this.setData({ list })
    } catch (e) {
      api.fail(e, '预约加载失败')
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 到馆签到：确认后扣课时 */
  async onSignin(e) {
    const { id, name } = e.currentTarget.dataset
    const confirmed = await api.confirm(
      `为「${name}」签到？\n签到后将自动扣减 1 课时`,
      '到馆签到'
    )
    if (!confirmed) return

    try {
      await api.call('booking', { action: 'signin', orderId: id }, { loading: true, loadingText: '签到中' })
      api.success('签到成功，已扣课时')
      this.load()
    } catch (err) {
      // 课时不足等业务错误直接展示
      api.fail(err)
    }
  },

  /** 取消会员预约 */
  async onCancel(e) {
    const { id, name } = e.currentTarget.dataset
    const confirmed = await api.confirm(`取消「${name}」的这条预约？名额将被释放。`, '取消预约')
    if (!confirmed) return

    try {
      await api.call('booking', { action: 'cancel', orderId: id }, { loading: true, loadingText: '取消中' })
      api.success('已取消')
      this.load()
    } catch (err) {
      api.fail(err)
    }
  }
})
