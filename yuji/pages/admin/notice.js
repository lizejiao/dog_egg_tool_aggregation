// pages/admin/notice.js
/**
 * 公告管理：新增 / 编辑 / 删除
 */
const api = require('../../../utils/api')
const guard = require('../../../utils/admin')
const fmt = require('../../../utils/format')

Page({
  data: {
    list: [],
    loading: true,
    showForm: false,
    formId: '',
    formTitle: '',
    formContent: ''
  },

  async onLoad() {
    const ok = await guard()
    if (!ok) return
    this._loaded = true
    this.load()
  },

  onShow() {
    if (this._loaded) this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    this.setData({ loading: true })
    try {
      const data = await api.call('notice', { action: 'list', limit: 20 })
      const list = (data.list || []).map((n) =>
        Object.assign({}, n, { timeText: fmt.fromNow(n.createTime) })
      )
      this.setData({ list })
    } catch (e) {
      api.fail(e, '公告加载失败')
    } finally {
      this.setData({ loading: false })
    }
  },

  onTitleInput(e) {
    this.setData({ formTitle: e.detail.value })
  },

  onContentInput(e) {
    this.setData({ formContent: e.detail.value })
  },

  onAdd() {
    this.setData({ showForm: true, formId: '', formTitle: '', formContent: '' })
  },

  onEdit(e) {
    const { id, title, content } = e.currentTarget.dataset
    this.setData({ showForm: true, formId: id, formTitle: title, formContent: content })
    // 滚到顶部方便编辑
    wx.pageScrollTo({ scrollTop: 0, duration: 200 })
  },

  onCancelForm() {
    this.setData({ showForm: false, formId: '', formTitle: '', formContent: '' })
  },

  async onSaveNotice() {
    const title = this.data.formTitle.trim()
    const content = this.data.formContent.trim()
    if (!title) {
      api.toast('请填写公告标题')
      return
    }
    if (!content) {
      api.toast('请填写公告内容')
      return
    }

    const confirmed = await api.confirm('确定保存这条公告吗？', '保存')
    if (!confirmed) return

    try {
      await api.call(
        'notice',
        {
          action: this.data.formId ? 'update' : 'create',
          id: this.data.formId || undefined,
          title,
          content
        },
        { loading: true, loadingText: '保存中' }
      )
      api.success('已保存')
      this.onCancelForm()
      this.load()
    } catch (e) {
      api.fail(e)
    }
  },

  async onDelete(e) {
    const { id, title } = e.currentTarget.dataset
    const confirmed = await api.confirm(`删除公告「${title}」？`, '删除公告')
    if (!confirmed) return
    try {
      await api.call('notice', { action: 'remove', id }, { loading: true })
      api.success('已删除')
      this.load()
    } catch (err) {
      api.fail(err)
    }
  }
})
