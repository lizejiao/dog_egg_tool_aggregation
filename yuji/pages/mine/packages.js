// pages/mine/packages.js
/**
 * 我的课时套餐：已购套餐使用进度 + 可购套餐展示
 */
const app = getApp()
const api = require('../../utils/api')
const fmt = require('../../utils/format')
const config = require('../../utils/config')

Page({
  data: {
    user: null,
    myPackages: [], // 已购套餐（含状态）
    shopPackages: [], // 在售套餐
    shopName: config.SHOP_NAME,
    shopPhone: config.SHOP_PHONE
  },

  onShow() {
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    try {
      const [user, pkgRes] = await Promise.all([
        app.ensureLogin(),
        api.call('package', { action: 'list' })
      ])

      const now = Date.now()
      const myPackages = (user.packages || []).map((p) => {
        const expired = p.expireAt && p.expireAt < now
        const usedUp = (p.usedHours || 0) >= p.totalHours
        return Object.assign({}, p, {
          percent: p.totalHours ? Math.round(((p.usedHours || 0) / p.totalHours) * 100) : 0,
          boughtText: fmt.formatDate(p.boughtAt),
          expireText: p.expireAt ? `${fmt.formatDate(p.expireAt)} 到期` : '长期有效',
          statusText: expired ? '已过期' : usedUp ? '已用完' : '使用中',
          statusClass: expired || usedUp ? 'tag-gray' : 'tag-green'
        })
      })

      const shopPackages = (pkgRes.list || []).map((p) =>
        Object.assign({}, p, { priceText: fmt.currency(p.price) })
      )

      this.setData({ user, myPackages, shopPackages })
    } catch (e) {
      api.fail(e, '套餐加载失败')
    }
  },

  /** 咨询购买：优先电话，未配置电话则提示到店 */
  onContact() {
    if (this.data.shopPhone) {
      wx.makePhoneCall({ phoneNumber: this.data.shopPhone }).catch(() => {})
    } else {
      api.alert(
        '课时套餐由馆主统一开通\n可到店咨询，或通过「我的 → 馆主入口」联系管理员',
        '联系馆主'
      )
    }
  }
})
