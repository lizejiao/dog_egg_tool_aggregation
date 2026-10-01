// index.js
/**
 * 首页：公告轮播 + 近期课程列表
 */
const app = getApp()
const api = require('../../utils/api')
const { decorateList } = require('../../utils/course')

const PAGE_SIZE = 10

Page({
  data: {
    greeting: '',
    user: null, // 当前会员（含剩余课时）
    notices: [], // 公告列表
    noticeIndex: 0,
    courses: [], // 课程列表（已格式化）
    loading: true,
    loadingMore: false,
    finished: false,
    skip: 0
  },

  onLoad() {
    this.setData({ greeting: this.getGreeting() })
    this.init()
  },

  // 从预约页返回时刷新课时与预约状态
  onShow() {
    if (this._inited) this.refresh()
  },

  onPullDownRefresh() {
    this.refresh().finally(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (!this.data.finished && !this.data.loadingMore) this.loadCourses(true)
  },

  /** 根据时间返回问候语 */
  getGreeting() {
    const h = new Date().getHours()
    if (h < 6) return '夜深了'
    if (h < 11) return '早安'
    if (h < 14) return '午安'
    if (h < 18) return '下午好'
    return '晚上好'
  },

  async init() {
    // 静默登录（失败不阻塞首页浏览）
    try {
      const user = await app.ensureLogin()
      this.setData({ user })
    } catch (e) {
      console.warn('[index] 登录失败', e)
    }
    this.loadNotices()
    await this.loadCourses(false)
    this._inited = true
  },

  /** 预约 / 取消后回到首页时同步数据 */
  async refresh() {
    try {
      const user = await app.refreshUser()
      this.setData({ user })
    } catch (e) {
      /* 忽略 */
    }
    await this.loadCourses(false)
  },

  async loadNotices() {
    try {
      const data = await api.call('notice', { action: 'list', limit: 5 })
      this.setData({ notices: data.list || [] })
    } catch (e) {
      console.warn('[index] 公告加载失败', e)
    }
  },

  /**
   * 加载课程列表
   * @param {boolean} append 是否追加（上拉加载更多）
   */
  async loadCourses(append) {
    const skip = append ? this.data.skip : 0
    if (append) this.setData({ loadingMore: true })
    else this.setData({ loading: true })

    try {
      const data = await api.call('course', {
        action: 'list',
        from: Date.now(), // 只看还没开始的课
        limit: PAGE_SIZE,
        skip
      })
      const list = decorateList(data.list)
      this.setData({
        courses: append ? this.data.courses.concat(list) : list,
        skip: skip + list.length,
        finished: list.length < PAGE_SIZE
      })
    } catch (e) {
      api.fail(e, '课程加载失败')
    } finally {
      this.setData({ loading: false, loadingMore: false })
    }
  },

  /** 点击公告：弹窗展示全文 */
  onNoticeTap() {
    const item = this.data.notices[this.data.noticeIndex]
    if (item) api.alert(item.content, item.title)
  },

  onNoticeChange(e) {
    this.setData({ noticeIndex: e.detail.current })
  },

  /** 点击课程卡片 → 课程详情 */
  onCourseTap(e) {
    wx.navigateTo({
      url: '/pages/course/detail?id=' + e.currentTarget.dataset.id
    })
  },

  onShareAppMessage() {
    return { title: '一起来练瑜伽吧', path: '/pages/index/index' }
  }
})
