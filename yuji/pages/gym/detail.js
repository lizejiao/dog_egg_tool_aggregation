// pages/gym/detail.js
/**
 * 馆详情：馆信息 + 该馆公告 + 近期课程表（学员预约入口）
 */
const app = getApp()
const api = require('../../utils/api')
const auth = require('../../utils/auth')
const { decorateList } = require('../../utils/course')
const fmt = require('../../utils/format')

const PAGE_SIZE = 10

Page({
  data: {
    id: '',
    gym: null,
    notices: [],
    courses: [],
    loading: true,
    loadingMore: false,
    finished: false,
    skip: 0,
    // 已学课程弹层
    historyShow: false,
    historyLoading: false,
    historyList: [],
    historyCount: 0,
    gymBalance: null // 该馆剩余课时（会员才显示）
  },

  onLoad(options) {
    this.setData({ id: options.id || '' })
    this.init()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (!this.data.finished && !this.data.loadingMore) this.loadCourses(true)
  },

  async init() {
    await this.load()
    this._inited = true
  },

  onShow() {
    // 从预约页返回时刷新
    if (this._inited) this.load()
  },

  async load() {
    try {
      const gymId = this.data.id
      const [gymRes, noticeRes] = await Promise.all([
        api.call('gym', { action: 'detail', gymId }),
        api.call('notice', { action: 'list', gymId, limit: 5 })
      ])
      this.setData({
        gym: gymRes.gym,
        notices: noticeRes.list || []
      })
      await this.loadCourses(false)
      // 已加入会员：拉取已学课程数 + 该馆剩余课时
      if (gymRes.gym && gymRes.gym.isJoined) {
        this.loadHistoryCount()
        // 强制从服务端刷新用户数据（馆主可能刚充了课时，本地缓存是旧的）
        try {
          await app.refreshUser()
        } catch (e) {
          /* 刷新失败则用本地缓存算 */
        }
        this.loadGymBalance()
      }
    } catch (e) {
      api.fail(e, '场馆信息加载失败')
      this.setData({ loading: false })
    }
  },

  /** 该馆剩余课时（从名下该馆的套餐明细算，有效未过期） */
  loadGymBalance() {
    const user = app.globalData.user
    if (!user || !user.packages) {
      this.setData({ gymBalance: 0 })
      return
    }
    const gymId = this.data.id
    const now = Date.now()
    let balance = 0
    user.packages.forEach((p) => {
      // 按馆隔离：只算该馆的套餐；老数据无 gymId 的不计入
      if (p.gymId === gymId && (p.usedHours || 0) < (p.totalHours || 0)) {
        if (!p.expireAt || p.expireAt > now) {
          balance += (p.totalHours || 0) - (p.usedHours || 0)
        }
      }
    })
    this.setData({ gymBalance: balance })
  },

  /** 已学课程总数（按钮角标用） */
  async loadHistoryCount() {
    try {
      const res = await api.call('booking', { action: 'mySignins', gymId: this.data.id, limit: 100 })
      this.setData({ historyCount: (res.list || []).length })
    } catch (e) {
      /* 忽略 */
    }
  },

  async loadCourses(append) {
    const gymId = this.data.id
    const skip = append ? this.data.skip : 0
    if (append) this.setData({ loadingMore: true })
    else this.setData({ loading: true })

    try {
      const data = await api.call('course', {
        action: 'list',
        gymId,
        from: Date.now(),
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

  /** 申请加入 / 撤回申请 / 退出场馆 */
  async onToggleJoin() {
    const gymId = this.data.id
    const gym = this.data.gym || {}

    // 游客：先引导登录
    if (!(await auth.ensureLogin('加入场馆需要先登录'))) return
    // 刚登录上：刷新馆详情（带出 isJoined/isPending）
    await this.load()

    const joined = !!(this.data.gym && this.data.gym.isJoined)
    const pending = !!(this.data.gym && this.data.gym.isPending)
    try {
      if (joined) {
        // 已是会员：查看已学课程
        this.onShowHistory()
      } else if (pending) {
        const confirmed = await api.confirm('撤回加入申请？馆主同意后才能加入成功', '撤回申请', '撤回')
        if (!confirmed) return
        await api.call('gym', { action: 'leave', gymId }, { loading: true, loadingText: '处理中' })
        api.toast('已撤回申请')
      } else {
        const confirmed = await api.confirm('申请后将等待馆主审核，馆主同意即可加入', '申请加入', '申请')
        if (!confirmed) return
        await api.call('gym', { action: 'join', gymId }, { loading: true, loadingText: '提交中' })
        api.success('申请已提交')
      }
      await this.load()
    } catch (e) {
      api.fail(e)
    }
  },

  /** 已学课程：打开弹层并拉取历史签到 */
  async onShowHistory() {
    this.setData({ historyShow: true, historyLoading: true, historyList: [] })
    try {
      const res = await api.call('booking', {
        action: 'mySignins',
        gymId: this.data.id,
        limit: 100
      })
      const list = (res.list || []).map((s) => ({
        _id: s._id,
        courseName: s.courseName || '课程',
        courseType: s.courseType || '',
        teacher: s.teacher || '',
        startTimeStr: s.startTimeStr || '',
        hours: s.hours || 1,
        signinTime: fmt.fromNow(s.signinTime)
      }))
      this.setData({ historyList: list, historyLoading: false })
    } catch (e) {
      api.fail(e, '已学课程加载失败')
      this.setData({ historyLoading: false })
    }
  },

  /** 关闭已学课程弹层 */
  onCloseHistory() {
    this.setData({ historyShow: false })
  },

  /** 弹层阻止冒泡 */
  stopPropagation() {},

  /** 拨打场馆电话 */
  onCallPhone() {
    const phone = this.data.gym && this.data.gym.phone
    if (!phone) {
      api.toast('该场馆暂未留联系电话')
      return
    }
    wx.makePhoneCall({ phoneNumber: phone }).catch(() => {})
  },

  /** 查看地图位置 */
  onViewMap() {
    const g = this.data.gym
    if (!g || !g.locateEnabled || !g.latitude) {
      api.toast('该场馆未开启定位')
      return
    }
    wx.openLocation({
      latitude: g.latitude,
      longitude: g.longitude,
      name: g.name,
      address: (g.city || '') + (g.address || ''),
      scale: 16
    }).catch(() => {})
  },

  onNoticeTap() {
    if (!this.data.notices.length) return
    if (this.data.notices.length === 1) {
      const n = this.data.notices[0]
      api.alert(n.content, n.title)
      return
    }
    wx.showActionSheet({
      itemList: this.data.notices.slice(0, 6).map((n) => (n.title || '').slice(0, 7)),
      success: (res) => {
        const n = this.data.notices[res.tapIndex]
        if (n) api.alert(n.content, n.title)
      }
    })
  },

  onCourseTap(e) {
    wx.navigateTo({
      url: '/pages/course/detail?id=' + e.currentTarget.dataset.id
    })
  },

  onShareAppMessage() {
    const g = this.data.gym || {}
    return {
      title: (g.name || '瑜伽馆') + ' · 一起练瑜伽吧',
      path: '/pages/gym/detail?id=' + this.data.id
    }
  }
})
