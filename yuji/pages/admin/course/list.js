// pages/admin/course/list.js
/**
 * 课程管理：列表 / 删除（编辑与新增跳转 edit 页）
 */
const api = require('../../../utils/api')
const guard = require('../../../utils/admin')
const { decorateList } = require('../../../utils/course')

const PAGE_SIZE = 50

Page({
  data: {
    list: [],
    loading: true
  },

  async onLoad() {
    // 管理员守卫：非管理员会被提示并退出页面
    const ok = await guard()
    if (!ok) return
    this._loaded = true
    this.load()
  },

  onShow() {
    // 从编辑页返回时刷新列表
    if (this._loaded) this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    this.setData({ loading: true })
    try {
      const data = await api.call('course', {
        action: 'list',
        includePast: true, // 后台显示全部课程
        order: 'desc',
        limit: PAGE_SIZE
      })
      this.setData({ list: decorateList(data.list) })
    } catch (e) {
      api.fail(e, '课程加载失败')
    } finally {
      this.setData({ loading: false })
    }
  },

  onAdd() {
    wx.navigateTo({ url: '/pages/admin/course/edit' })
  },

  onEdit(e) {
    wx.navigateTo({ url: '/pages/admin/course/edit?id=' + e.currentTarget.dataset.id })
  },

  async onDelete(e) {
    const { id, name } = e.currentTarget.dataset
    const confirmed = await api.confirm(
      `删除「${name}」后不可恢复，已预约的会员会收到取消记录。确定删除吗？`,
      '删除课程'
    )
    if (!confirmed) return

    try {
      const res = await api.call('course', { action: 'remove', id }, { loading: true, loadingText: '删除中' })
      api.success('已删除')
      if (res && res.canceledOrders > 0) {
        api.alert(`已同步取消 ${res.canceledOrders} 条预约`, '提示')
      }
      this.load()
    } catch (err) {
      // code 4001 表示还有待处理预约，需要二次确认
      if (err.code === 4001) {
        const force = await api.confirm(err.message, '确认删除')
        if (force) {
          try {
            await api.call('course', { action: 'remove', id, force: true }, { loading: true })
            api.success('已删除')
            this.load()
          } catch (e2) {
            api.fail(e2)
          }
        }
      } else {
        api.fail(err)
      }
    }
  }
})
