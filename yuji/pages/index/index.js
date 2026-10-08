// index.js
/**
 * 首页（角色化视图）
 * ------------------------------------------------------------------
 * 游客 guest     ：未登录/登录失败 → 附近（当前城市）瑜伽馆列表
 * 学员 student  ：同游客视图（可进馆约课），顶部展示剩余课时
 * 馆主 owner    ：今日预约概览 + 本馆近期课程（管理视角）
 * 超管 superadmin：平台概览（场馆数/馆主数/学员数）+ 全部场馆
 * ------------------------------------------------------------------
 */
const app = getApp()
const api = require('../../utils/api')
const { decorateList } = require('../../utils/course')
const location = require('../../utils/location')

const PAGE_SIZE = 10

Page({
  data: {
    greeting: '',
    role: 'guest', // guest / student / owner / superadmin / admin
    user: null,
    // 游客/学员视图：城市 + 附近瑜伽馆
    city: '',
    cityPickerShow: false,
    gyms: [],
    joinedGyms: [], // 学员：已加入的瑜伽馆
    otherGyms: [], // 学员：未加入的瑜伽馆
    gymLoading: true,
    // 馆主视图
    myGym: null,
    todayGroups: [],
    todayStats: { courses: 0, booked: 0, signed: 0 },
    ownerCourses: [],
    joinRequests: [], // 待审批的加入申请
    // 超管视图
    platform: { gyms: 0, owners: 0, students: 0, todayOrders: 0 },
    gymList: [],
    // 授权馆主弹层
    ownerPickerShow: false,
    ownerPickerGym: null, // 正在授权的场馆
    ownerKeyword: '',
    userList: [],
    userLoading: false
  },

  onLoad() {
    this.setData({ greeting: this.getGreeting(), city: location.getCity() })
    this.init()
  },

  onShow() {
    if (this._inited) this.refresh()
  },

  onPullDownRefresh() {
    this.refresh().finally(() => wx.stopPullDownRefresh())
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
    // 静默登录（游客可浏览：登录失败按游客处理，不阻塞）
    let role = 'guest'
    let user = null
    try {
      user = await app.ensureLogin()
      role = user.role || 'student'
    } catch (e) {
      console.warn('[index] 未登录，按游客浏览', e && e.message)
    }
    this.setData({ role, user })

    if (role === 'owner' || role === 'admin') this.loadOwnerView()
    else if (role === 'superadmin') this.loadSuperView()
    else this.loadGyms() // 游客 / 学员：附近瑜伽馆

    this._inited = true
  },

  /** 回到首页时同步数据（预约/角色变化） */
  async refresh() {
    try {
      const user = await app.refreshUser()
      const role = user.role || 'student'
      if (role !== this.data.role) {
        // 角色发生变化，整体重载（如刚被任命为馆主 / 退出登录）
        this.setData({ role, user })
        this.init()
        return
      }
      this.setData({ user })
    } catch (e) {
      // 退出登录后回到首页 → 游客视图
      if (this.data.role !== 'guest' && this.data.role !== 'student') {
        this.setData({ role: 'guest', user: null })
        this.loadGyms()
        return
      }
    }

    const role = this.data.role
    if (role === 'owner' || role === 'admin') this.loadOwnerView()
    else if (role === 'superadmin') this.loadSuperView()
    else this.loadGyms()
  },

  /* ==================== 游客 / 学员视图：附近瑜伽馆 ==================== */

  async loadGyms() {
    const city = this.data.city
    this.setData({ gymLoading: true })
    try {
      const data = await api.call('gym', { action: 'list', city })
      const gyms = location.decorateWithDistance(data.list || [], null)
      // 学员：拆分已加入 / 未加入
      const joinedGyms = gyms.filter((g) => g.isJoined)
      const otherGyms = gyms.filter((g) => !g.isJoined)
      this.setData({ gyms, joinedGyms, otherGyms, gymLoading: false })
    } catch (e) {
      console.warn('[index] 场馆列表加载失败', e)
      this.setData({ gyms: [], joinedGyms: [], otherGyms: [], gymLoading: false })
    }
  },

  /** 打开城市选择器 */
  openCityPicker() {
    this.setData({ cityPickerShow: true })
  },
  closeCityPicker() {
    this.setData({ cityPickerShow: false })
  },

  /** 选择城市（组件事件） */
  onCitySelectEvent(e) {
    const city = e.detail.city
    if (!city || city === this.data.city) {
      this.closeCityPicker()
      return
    }
    location.setCity(city)
    this.setData({ city, cityPickerShow: false })
    this.loadGyms()
  },

  /** 进入馆详情 */
  onGymTap(e) {
    wx.navigateTo({
      url: '/pages/gym/detail?id=' + e.currentTarget.dataset.id
    })
  },

  /* ==================== 馆主视图 ==================== */

  async loadOwnerView() {
    try {
      const gyms = app.getMyGyms()
      const myGym = gyms && gyms.length ? gyms[0] : null
      this.setData({ myGym })
      if (!myGym) return

      const [allRes, courseRes, reqRes] = await Promise.all([
        api.call('booking', { action: 'listAll', gymId: myGym._id, limit: 100 }),
        api.call('course', { action: 'list', from: Date.now(), limit: 10, gymId: myGym._id }),
        api.call('gym', { action: 'pendingList', gymId: myGym._id }).catch(() => ({ list: [] }))
      ])

      // 有效预约（待上课+已签到），按天分组
      const orders = (allRes.list || []).filter(
        (o) => o.status === 'booked' || o.status === 'signed'
      )

      // 解析预约的上课日期：以课程快照为准（startTimeStr 首段 > startTime 时间戳 > dateKey 兜底）
      const p2 = (n) => (n < 10 ? '0' + n : '' + n)
      const today = new Date()
      today.setHours(0, 0, 0, 0)
      const todayKey = `${today.getFullYear()}-${p2(today.getMonth() + 1)}-${p2(today.getDate())}`

      const keyOf = (o) => {
        if (o.startTimeStr) {
          const k = String(o.startTimeStr).split(' ')[0]
          if (/^\d{4}-\d{2}-\d{2}$/.test(k)) return k
        }
        if (o.startTime) {
          const d = new Date(o.startTime)
          return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
        }
        return o.dateKey || ''
      }
      const dayDiff = (key) => {
        const [y, m, d] = key.split('-').map(Number)
        const that = new Date(y, m - 1, d)
        return Math.round((that.getTime() - today.getTime()) / 86400000)
      }

      // 只保留今天及以后的预约，按日期分组
      const byDay = {}
      let upcomingBooked = 0
      orders.forEach((o) => {
        const key = keyOf(o)
        if (!key) return
        if (dayDiff(key) < 0) return // 过期预约不展示（canceled 已被过滤，这里挡历史 signed）
        if (!byDay[key]) byDay[key] = []
        byDay[key].push(o)
        if (o.status === 'booked') upcomingBooked++
      })

      // 组装分组：按日期升序；行内显示具体日期（10月4日）
      const todayGroups = Object.keys(byDay)
        .sort()
        .map((key) => {
          const dateText = `${Number(key.slice(5, 7))}月${Number(key.slice(8, 10))}日`
          return { key, dateText, groups: this.groupByCourse(byDay[key], dateText) }
        })

      const todayOrders = byDay[todayKey] || []
      const ownerCourses = decorateList(courseRes.list)

      this.setData({
        todayGroups,
        todayStats: {
          courses: todayOrders.length ? this.groupByCourse(todayOrders).length : 0,
          booked: upcomingBooked, // 全部待上课
          signed: todayOrders.filter((o) => o.status === 'signed').length
        },
        ownerCourses,
        joinRequests: reqRes.list || []
      })
    } catch (e) {
      console.warn('[index] 馆主视图加载失败', e)
    }
  },

  /** 预约按课程分组（馆主预约课程视图用）；dateText 为行内日期 */
  groupByCourse(orders, dateText) {
    const map = {}
    const arr = []
    for (const o of orders) {
      const key = o.courseId || 'unknown'
      if (!map[key]) {
        map[key] = {
          courseId: key,
          courseName: o.courseName || '已删除课程',
          timeText: o.startTimeStr ? String(o.startTimeStr).split(' ')[1] || '' : '',
          dateText: dateText || '',
          teacher: o.teacher || '',
          members: [],
          total: 0,
          signed: 0
        }
        arr.push(map[key])
      }
      map[key].total++
      if (o.status === 'signed') map[key].signed++
      map[key].members.push({
        orderId: o._id,
        nickName: o.nickName || '会员',
        phone: o.phone || '',
        status: o.status,
        statusText: o.status === 'signed' ? '已签到' : '待上课'
      })
    }
    arr.sort((a, b) => (a.timeText > b.timeText ? 1 : -1))
    return arr
  },

  goOrders() {
    wx.navigateTo({ url: '/pages/admin/order/list' })
  },
  goCourseManage() {
    wx.navigateTo({ url: '/pages/admin/course/list' })
  },
  goAdminHome() {
    wx.navigateTo({ url: '/pages/admin/home' })
  },
  goMine() {
    wx.switchTab({ url: '/pages/mine/mine' })
  },

  /** 馆主：同意加入申请 */
  async onApproveJoin(e) {
    const requestId = e.currentTarget.dataset.id
    try {
      await api.call('gym', { action: 'approve', requestId }, { loading: true, loadingText: '通过中' })
      api.success('已同意')
      this.loadOwnerView()
    } catch (err) {
      api.fail(err)
    }
  },

  /** 馆主：拒绝加入申请 */
  async onRejectJoin(e) {
    const requestId = e.currentTarget.dataset.id
    try {
      await api.call('gym', { action: 'reject', requestId }, { loading: true, loadingText: '处理中' })
      api.toast('已拒绝')
      this.loadOwnerView()
    } catch (err) {
      api.fail(err)
    }
  },

  /** 点击课程卡片 → 课程详情 */
  onCourseTap(e) {
    wx.navigateTo({
      url: '/pages/course/detail?id=' + e.currentTarget.dataset.id
    })
  },

  /* ==================== 超管视图 ==================== */

  async loadSuperView() {
    try {
      const [gymRes, statsRes] = await Promise.all([
        api.call('gym', { action: 'allList' }),
        api.call('member', { action: 'stats' })
      ])
      const gymList = gymRes.list || []
      const ov = statsRes.overview || {}
      this.setData({
        platform: {
          gyms: gymList.length,
          owners: gymList.filter((g) => g.ownerOpenid).length,
          students: ov.members || 0,
          todayOrders: ov.bookedOrders || 0
        },
        gymList
      })
    } catch (e) {
      console.warn('[index] 超管视图加载失败', e)
    }
  },

  /** 超管：创建场馆 */
  goCreateGym() {
    wx.navigateTo({ url: '/pages/super/gym-edit' })
  },

  /** 超管：编辑场馆 */
  goEditGym(e) {
    wx.navigateTo({ url: '/pages/super/gym-edit?id=' + e.currentTarget.dataset.id })
  },

  /** 超管：进馆详情（会员视角看课程、预约） */
  goGymDetail(e) {
    const closed = e.currentTarget.dataset.closed
    if (closed) {
      api.toast('场馆已关闭，无法预约')
      return
    }
    wx.navigateTo({ url: '/pages/gym/detail?id=' + e.currentTarget.dataset.id })
  },

  /** 超管：打开授权馆主弹层（授权 / 更换） */
  async onAssignOwner(e) {
    const gymId = e.currentTarget.dataset.id
    const gym = this.data.gymList.find((g) => g._id === gymId)
    if (!gym) return
    this.setData({
      ownerPickerShow: true,
      ownerPickerGym: gym,
      ownerKeyword: '',
      userList: []
    })
    this.loadUsers('')
  },

  /** 拉取注册用户列表（空关键词=全部） */
  async loadUsers(keyword) {
    this.setData({ userLoading: true })
    try {
      const res = await api.call('gym', { action: 'searchUsers', keyword })
      this.setData({ userList: res.list || [], userLoading: false })
    } catch (err) {
      api.fail(err)
      this.setData({ userLoading: false })
    }
  },

  /** 用户搜索输入 */
  onOwnerSearchInput(e) {
    const kw = e.detail.value || ''
    this.setData({ ownerKeyword: kw })
    // 防抖 300ms
    if (this._searchTimer) clearTimeout(this._searchTimer)
    this._searchTimer = setTimeout(() => {
      this.loadUsers(this.data.ownerKeyword.trim())
    }, 300)
  },

  /** 关闭授权弹层 */
  closeOwnerPicker() {
    this.setData({ ownerPickerShow: false, ownerPickerGym: null })
  },

  stopPropagation() {
    /* 阻止弹层冒泡关闭 */
  },

  /** 选中用户 → 确认授权为馆主 */
  async onPickUser(e) {
    const { openid, name, superadmin } = e.currentTarget.dataset
    const gym = this.data.ownerPickerGym
    if (!gym || superadmin) return

    const isChange = !!gym.ownerOpenid
    const confirmed = await api.confirm(
      (isChange ? `将「${gym.name}」的馆主从「${gym.ownerName || '原馆主'}」更换为「${name}」？`
                : `确定授权「${name}」为「${gym.name}」的馆主？`) +
        '\n\n授权立即生效，对方登录后即可管理该场馆',
      isChange ? '更换馆主' : '授权馆主',
      isChange ? '更换' : '授权'
    )
    if (!confirmed) return

    try {
      await api.call('gym', { action: 'setOwner', gymId: gym._id, ownerOpenid: openid }, { loading: true, loadingText: '授权中' })
      api.success(isChange ? '馆主已更换' : '授权成功')
      this.closeOwnerPicker()
      this.loadSuperView() // 刷新列表（馆主名/已任命状态）
    } catch (err) {
      api.fail(err)
    }
  },

  /** 超管：关闭/重开场馆 */
  async onToggleGymStatus(e) {
    const gymId = e.currentTarget.dataset.id
    const closed = e.currentTarget.dataset.closed
    const confirmed = await api.confirm(
      closed ? '重新开放该场馆？' : '关闭后学员端不再展示该场馆（历史数据保留），确定关闭？',
      closed ? '重开场馆' : '关闭场馆',
      closed ? '重开' : '关闭'
    )
    if (!confirmed) return
    try {
      await api.call('gym', { action: 'close', gymId, reopen: closed }, { loading: true })
      api.success('已' + (closed ? '重开' : '关闭'))
      this.loadSuperView()
    } catch (err) {
      api.fail(err)
    }
  },

  onShareAppMessage() {
    return { title: '来找一家身边的瑜伽馆吧', path: '/pages/index/index' }
  }
})
