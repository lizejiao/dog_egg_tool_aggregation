/**
 * 课程卡片
 * -----------------------------
 * props:
 *   course  课程对象（需先由调用方用 utils/course.js 归一化）
 * 事件：
 *   bindtap  点击卡片
 */
Component({
  options: {
    addGlobalClass: true // 允许使用 app.wxss 里的 tag 样式
  },
  properties: {
    course: {
      type: Object,
      value: {}
    }
  },
  methods: {
    onTap() {
      // 自定义事件名用 select，避免与原生 tap 冒泡重复触发
      this.triggerEvent('select', { course: this.data.course })
    }
  }
})
