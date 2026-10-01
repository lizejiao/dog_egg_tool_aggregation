// pages/admin/member/detail.js
/**
 * 会员详情：增减课时 / 开通套餐 / 课时流水 / 签到记录
 */
const api = require('../../../utils/api')
const guard = require('../../../utils/admin')
const fmt = require('../../../utils/format')
const C = require('../../../utils/constants')

Page({
  data: {
    ready: false,
    openid: '',
    user: null,
    avatarText: '客',
    myPackages: [],
    logs: [],
    signins: [],
    activeTab: 0,
    shopPackages: []
  },

  async onLoad(options) {
    if (!(await guard())) return
    this.setData({ ready: true, openid: options.openid || '' })
    this.load()
  },

  async load() {
    if (!this.data.openid) return
    try {
      const [detailRes, pkgRes] = await Promise.all([
        api.call('member', { action: 'detail', openid: this.data.openid }),
        api.call('package', { action: 'list' })
      ])

      const user = detailRes.user
      const now = Date.now()
      const myPackages = (user.packages || []).map((p) => {
        const expired = p.expireAt && p.expireAt < now
        const usedUp = (p.usedHours || 0) >= p.totalHours
        return Object.assign({}, p, {
          expireText: p.expireAt ? ` · ${fmt.formatDate(p.expireAt)} 到期` : ' · 长期有效',
          statusText: expired ? '已过期' : usedUp ? '已用完' : '使用中',
          statusClass: expired || usedUp ? 'tag-gray' : 'tag-green'
        })
      })

      const logs = (detailRes.hoursLogs || []).map((l) =>
        Object.assign({}, l, {
          typeText: C.HOURS_LOG_TEXT[l.type] || l.type,
          timeText: fmt.formatDateTime(l.createTime)
        })
      )

      const signins = (detailRes.signins || []).map((s) =>
        Object.assign({}, s, { signinText: fmt.formatDateTime(s.signinTime) })
      )

      this.setData({
        user,
        avatarText: (user.nickName || '客')[0],
        myPackages,
        logs,
        signins,
        shopPackages: pkgRes.list || []
      })
    } catch (e) {
      api.fail(e, '会员信息加载失败')
    }
  },

  onTabChange(e) {
    this.setData({ activeTab: Number(e.currentTarget.dataset.index) })
  },

  /** 通用：输入课时数 + 原因，然后调用 adjust */
  async adjustHours(direction) {
    const amountStr = await api.prompt(
      direction > 0 ? '增加课时' : '扣减课时',
      '请输入课时数（正整数）',
      ''
    )
    if (!amountStr) return
    const amount = parseInt(amountStr, 10)
    if (!amount || amount < 1 || amount > 200) {
      api.toast('请输入 1-200 的整数')
      return
    }

    const reason = await api.prompt('调整原因（选填）', '如：线下转账购课 / 赠送课时', '')
    if (reason === null) return

    const user = this.data.user
    const confirmed = await api.confirm(
      `${user.nickName || '该会员'}：${direction > 0 ? '增加' : '扣减'} ${amount} 课时\n当前剩余 ${user.balance} 课时`,
      '确认调整'
    )
    if (!confirmed) return

    try {
      const res = await api.call(
        'member',
        {
          action: 'adjust',
          openid: this.data.openid,
          amount: direction * amount,
          reason: reason || (direction > 0 ? '手动增加' : '手动扣减')
        },
        { loading: true, loadingText: '调整中' }
      )
      api.success('调整成功')
      this.load()
      return res
    } catch (e) {
      api.fail(e)
    }
  },

  onAddHours() {
    this.adjustHours(1)
  },

  onDeductHours() {
    this.adjustHours(-1)
  },

  /** 开通套餐：ActionSheet 选择后确认 */
  async onRecharge() {
    const list = this.data.shopPackages
    if (!list.length) {
      api.toast('请先在云数据库 package 集合中添加套餐')
      return
    }
    const items = list.slice(0, 6).map((p) => `${p.name}｜${p.totalHours}课时｜${fmt.currency(p.price)}`)

    wx.showActionSheet({
      itemList: items,
      success: async (res) => {
        const pkg = list[res.tapIndex]
        const confirmed = await api.confirm(
          `为「${this.data.user.nickName || '该会员'}」开通\n${pkg.name}：${pkg.totalHours} 课时，${fmt.currency(pkg.price)}`,
          '确认开通'
        )
        if (!confirmed) return
        try {
          await api.call(
            'member',
            { action: 'recharge', openid: this.data.openid, packageId: pkg._id },
            { loading: true, loadingText: '开通中' }
          )
          api.success('已开通')
          this.load()
        } catch (e) {
          api.fail(e)
        }
      }
    }).catch(() => {})
  }
})
