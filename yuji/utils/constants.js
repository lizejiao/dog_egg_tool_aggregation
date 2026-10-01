/**
 * 全局枚举 / 常量
 * 所有状态值都集中在这里，改文案只需要改这一个文件。
 */

// 用户角色
const ROLE = {
  STUDENT: 'student',
  ADMIN: 'admin'
}

// 预约记录状态
const ORDER_STATUS = {
  BOOKED: 'booked', // 已预约
  SIGNED: 'signed', // 已签到（已消课）
  CANCELED: 'canceled' // 已取消
}

const ORDER_STATUS_TEXT = {
  booked: '已预约',
  signed: '已签到',
  canceled: '已取消'
}

// 订单状态对应的标签样式类（app.wxss / tag 组件）
const ORDER_STATUS_CLASS = {
  booked: 'tag-green',
  signed: 'tag-blue',
  canceled: 'tag-gray'
}

// 课程业务状态（存库字段，由管理员控制）
const COURSE_STATUS = {
  OPEN: 'open', // 正常开放
  CLOSED: 'closed' // 已停课
}

// 课程展示状态（前端根据 停课 / 满员 / 时间 计算得出）
const COURSE_DISPLAY = {
  BOOKABLE: 'bookable', // 可预约
  FULL: 'full', // 已约满
  CLOSED: 'closed', // 已停课
  FINISHED: 'finished' // 已结束
}

const COURSE_DISPLAY_TEXT = {
  bookable: '可预约',
  full: '已约满',
  closed: '已停课',
  finished: '已结束'
}

const COURSE_DISPLAY_CLASS = {
  bookable: 'tag-green',
  full: 'tag-orange',
  closed: 'tag-gray',
  finished: 'tag-gray'
}

// 课时流水类型
const HOURS_LOG_TYPE = {
  RECHARGE: 'recharge', // 购买套餐增加
  SIGNIN: 'signin', // 签到消课
  ADD: 'add', // 管理员手动增加
  DEDUCT: 'deduct' // 管理员手动扣减
}

const HOURS_LOG_TEXT = {
  recharge: '套餐充值',
  signin: '课程签到',
  add: '手动增加',
  deduct: '手动扣减'
}

// 瑜伽类型预设（新增课程时快捷选择，ActionSheet 最多支持 6 项）
const YOGA_TYPES = ['哈他瑜伽', '流瑜伽', '阴瑜伽', '空中瑜伽', '普拉提', '孕产瑜伽']

// 常用上课地点预设
const LOCATIONS = ['A 教室（大班）', 'B 教室（小班）', '私教室', '空中瑜伽室']

// 老师预设
const TEACHERS = ['Luna', 'Mia', 'Coco', 'Ella']

module.exports = {
  ROLE,
  ORDER_STATUS,
  ORDER_STATUS_TEXT,
  ORDER_STATUS_CLASS,
  COURSE_STATUS,
  COURSE_DISPLAY,
  COURSE_DISPLAY_TEXT,
  COURSE_DISPLAY_CLASS,
  HOURS_LOG_TYPE,
  HOURS_LOG_TEXT,
  YOGA_TYPES,
  LOCATIONS,
  TEACHERS
}
