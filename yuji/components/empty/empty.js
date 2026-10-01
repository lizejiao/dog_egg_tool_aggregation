/**
 * 空状态组件
 * props: icon / title / sub
 */
Component({
  options: {
    addGlobalClass: true
  },
  properties: {
    icon: { type: String, value: '🌿' },
    title: { type: String, value: '暂无内容' },
    sub: { type: String, value: '' }
  }
})
