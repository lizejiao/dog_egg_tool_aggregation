// pages/super/gym-edit.js
/**
 * 超管：创建/编辑场馆
 */
const api = require('../../utils/api')

Page({
  data: {
    isEdit: false,
    gymId: '',
    name: '',
    city: '',
    address: '',
    phone: '',
    desc: '',
    submitting: false,
    // 城市选择
    cityPickerShow: false
  },

  onLoad(options) {
    if (options.id) {
      // 编辑模式：拉取现有馆
      this.setData({ isEdit: true, gymId: options.id })
      this.load()
    }
  },

  async load() {
    try {
      const res = await api.call('gym', { action: 'detail', gymId: this.data.gymId })
      const g = res.gym || {}
      this.setData({
        name: g.name || '',
        city: g.city || '',
        address: g.address || '',
        phone: g.phone || '',
        desc: g.desc || ''
      })
      wx.setNavigationBarTitle({ title: '编辑场馆' })
    } catch (e) {
      api.fail(e, '场馆信息加载失败')
    }
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [field]: e.detail.value })
  },

  /* ---------- 城市选择 ---------- */
  openCityPicker() {
    this.setData({ cityPickerShow: true })
  },
  closeCityPicker() {
    this.setData({ cityPickerShow: false })
  },
  onCitySelectEvent(e) {
    this.setData({ city: e.detail.city, cityPickerShow: false })
  },

  /* ---------- 提交 ---------- */
  async onSubmit() {
    const { name, city, address, phone, desc, isEdit, gymId, submitting } = this.data
    if (submitting) return
    if (!name.trim()) return api.toast('请填写场馆名称')
    if (!city) return api.toast('请选择所在城市')

    this.setData({ submitting: true })
    try {
      const payload = {
        action: isEdit ? 'update' : 'create',
        name: name.trim(),
        city,
        address: address.trim(),
        phone: phone.trim(),
        desc: desc.trim()
      }
      if (isEdit) payload.gymId = gymId
      await api.call('gym', payload, { loading: true, loadingText: '保存中' })
      api.success(isEdit ? '已保存' : '场馆已创建')
      setTimeout(() => wx.navigateBack(), 600)
    } catch (e) {
      api.fail(e)
    } finally {
      this.setData({ submitting: false })
    }
  }
})
