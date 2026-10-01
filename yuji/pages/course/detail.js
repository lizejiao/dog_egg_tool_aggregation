// pages/course/detail.js
/**
 * 课程详情：课程信息展示 + 预约 / 取消预约
 */
const app = getApp()
const api = require('../../utils/api')
const { decorate } = require('../../utils/course')
const { HOURS_PER_CLASS } = require('../../utils/config')

Page({
  data: {
    id: '',
    course: null,
    myOrder: null, // 当前用户在该课程的预约（booked / signed）
    balance: 0, // 剩余课时
    canBook: false,
    bookButtonText: '立即预约',
    submitting: false
  },

  onLoad(options) {
    this.setData({ id: options.id || '' })
  },

  onShow() {
    this.load()
  },

  /** 拉取课程详情 + 我的预约状态 */
  async load() {
    if (!this.data.id) return
    try {
      // 未登录时也允许浏览详情，只是不查预约状态
      const [detailRes, mineRes] = await Promise.all([
        api.call('course', { action: 'detail', id: this.data.id }),
        app
          .ensureLogin()
          .then(() => api.call('booking', { action: 'listMine', courseId: this.data.id }))
          .catch(() => ({ list: [] }))
      ])

      const course = decorate(detailRes.course)
      const myOrder =
        (mineRes.list || []).find((o) => o.status === 'booked' || o.status === 'signed') || null

      this.setData({ course, myOrder })
      this.updateButtonState(course, myOrder)
    } catch (e) {
      api.fail(e, '课程加载失败')
    }
  },

  /** 计算底部按钮文案与可用状态 */
  updateButtonState(course, myOrder) {
    const user = app.globalData.user
    const balance = user ? user.balance || 0 : 0

    let canBook = false
    let text = '立即预约'

    if (course.display === 'finished' || course.display === 'closed') {
      text = '该课程已' + (course.display === 'closed' ? '停课' : '结束')
    } else if (course.display === 'full') {
      text = '名额已满'
    } else if (balance < HOURS_PER_CLASS) {
      text = '课时不足，去充值'
    } else {
      canBook = true
    }

    this.setData({ balance, canBook, bookButtonText: text })
  },

  /** 预约 */
  async onBook() {
    const { course, myOrder, canBook } = this.data
    if (this.data.submitting) return

    // 课时不足：跳转到我的套餐页
    if (course && course.display === 'bookable' && this.data.balance < HOURS_PER_CLASS && !myOrder) {
      wx.navigateTo({ url: '/pages/mine/packages' })
      return
    }
    if (!canBook) {
      api.toast('当前无法预约该课程')
      return
    }

    const confirmed = await api.confirm(
      `预约「${course.name}」\n${course.startTimeStr} ${course.timeRange}\n签到后将扣减 ${HOURS_PER_CLASS} 课时`,
      '确认预约'
    )
    if (!confirmed) return

    this.setData({ submitting: true })
    try {
      await api.call('booking', { action: 'create', courseId: course._id }, { loading: true, loadingText: '预约中' })
      await app.refreshUser()
      api.success('预约成功')
      this.load()
    } catch (e) {
      api.fail(e)
      // 课时发生变化时刷新按钮状态
      this.load()
    } finally {
      this.setData({ submitting: false })
    }
  },

  /** 取消预约 */
  async onCancel() {
    const { myOrder } = this.data
    if (!myOrder) return

    const confirmed = await api.confirm('取消后将释放名额，确定取消这条预约吗？', '取消预约')
    if (!confirmed) return

    try {
      await api.call('booking', { action: 'cancel', orderId: myOrder._id }, { loading: true, loadingText: '取消中' })
      await app.refreshUser()
      api.success('已取消')
      this.load()
    } catch (e) {
      api.fail(e)
    }
  }
})
