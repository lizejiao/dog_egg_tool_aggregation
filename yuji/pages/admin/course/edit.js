// pages/admin/course/edit.js
/**
 * 课程新增 / 编辑表单
 * 带 id 参数 = 编辑；不带 = 新增
 */
const api = require('../../../utils/api')
const guard = require('../../../utils/admin')
const { YOGA_TYPES, LOCATIONS, TEACHERS, COURSE_STATUS } = require('../../../utils/constants')

Page({
  data: {
    ready: false,
    id: '',
    today: '',
    teachers: TEACHERS,
    locations: LOCATIONS,
    form: {
      name: '',
      type: '',
      teacher: '',
      location: '',
      date: '',
      time: '',
      duration: '60',
      capacity: '12',
      desc: '',
      status: COURSE_STATUS.OPEN
    },
    submitting: false
  },

  async onLoad(options) {
    if (!(await guard())) return
    const now = new Date()
    const p = (n) => (n < 10 ? '0' + n : '' + n)
    const today = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`

    this.setData({ ready: true, today, id: options.id || '' })
    if (options.id) {
      wx.setNavigationBarTitle({ title: '编辑课程' })
      this.loadCourse(options.id)
    }
  },

  /** 编辑时回填表单 */
  async loadCourse(id) {
    try {
      const res = await api.call('course', { action: 'detail', id })
      const c = res.course
      this.setData({
        form: {
          name: c.name,
          type: c.type,
          teacher: c.teacher,
          location: c.location,
          date: c.dateKey || '',
          time: c.startTimeStr ? c.startTimeStr.slice(11, 16) : '',
          duration: String(c.duration || 60),
          capacity: String(c.capacity || 12),
          desc: c.desc || '',
          status: c.status || COURSE_STATUS.OPEN
        }
      })
    } catch (e) {
      api.fail(e, '课程加载失败')
      setTimeout(() => wx.navigateBack(), 900)
    }
  },

  /** 普通输入框 */
  onInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: e.detail.value })
  },

  /** 日期 / 时间选择器 */
  onPickerChange(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: e.detail.value })
  },

  onPickType() {
    wx.showActionSheet({
      itemList: YOGA_TYPES,
      success: (res) => this.setData({ 'form.type': YOGA_TYPES[res.tapIndex] })
    }).catch(() => {})
  },

  onPickLocation() {
    wx.showActionSheet({
      itemList: LOCATIONS,
      success: (res) => this.setData({ 'form.location': LOCATIONS[res.tapIndex] })
    }).catch(() => {})
  },

  onChipTap(e) {
    const { value, field } = e.currentTarget.dataset
    this.setData({ [`form.${field}`]: value })
  },

  onStatusChange(e) {
    this.setData({ 'form.status': e.detail.value ? COURSE_STATUS.OPEN : COURSE_STATUS.CLOSED })
  },

  /** 提交前本地校验（云函数里还会再校验一遍） */
  validate() {
    const f = this.data.form
    if (!f.name.trim()) return '请填写课程名称'
    if (!f.type) return '请选择瑜伽类型'
    if (!f.teacher.trim()) return '请填写授课老师'
    if (!f.location.trim()) return '请填写上课地点'
    if (!f.date) return '请选择上课日期'
    if (!f.time) return '请选择开始时间'
    const duration = parseInt(f.duration, 10)
    if (!duration || duration < 15 || duration > 300) return '课程时长需为 15-300 分钟'
    const capacity = parseInt(f.capacity, 10)
    if (!capacity || capacity < 1 || capacity > 200) return '容纳人数需为 1-200'
    return ''
  },

  async onSave() {
    if (this.data.submitting) return
    const msg = this.validate()
    if (msg) {
      api.toast(msg)
      return
    }

    const confirmed = await api.confirm('确定保存课程信息吗？', '保存')
    if (!confirmed) return

    this.setData({ submitting: true })
    const f = this.data.form
    const payload = {
      action: this.data.id ? 'update' : 'create',
      id: this.data.id || undefined,
      name: f.name,
      type: f.type,
      teacher: f.teacher,
      location: f.location,
      date: f.date,
      time: f.time,
      duration: parseInt(f.duration, 10),
      capacity: parseInt(f.capacity, 10),
      desc: f.desc,
      status: f.status
    }

    try {
      await api.call('course', payload, { loading: true, loadingText: '保存中' })
      api.success('已保存')
      setTimeout(() => wx.navigateBack(), 600)
    } catch (e) {
      api.fail(e)
    } finally {
      this.setData({ submitting: false })
    }
  }
})
