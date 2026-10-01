/**
 * 课程数据归一化：把数据库记录转成展示用对象
 * 列表页 / 详情页 / 管理后台统一用这一个函数，保证展示口径一致
 */
const fmt = require('./format')
const C = require('./constants')

function decorate(course) {
  const start = fmt.toDate(course.startTime)
  const now = Date.now()

  // 计算展示状态：停课 > 已结束 > 已满 > 可预约
  let display = C.COURSE_DISPLAY.BOOKABLE
  if (course.status === C.COURSE_STATUS.CLOSED) {
    display = C.COURSE_DISPLAY.CLOSED
  } else if (start && start.getTime() < now) {
    display = C.COURSE_DISPLAY.FINISHED
  } else if (course.booked >= course.capacity) {
    display = C.COURSE_DISPLAY.FULL
  }

  const remain = Math.max(0, (course.capacity || 0) - (course.booked || 0))

  return Object.assign({}, course, {
    monthDay: fmt.formatMonthDay(start),
    dayLabel: fmt.dayLabel(start),
    weekday: fmt.weekday(start),
    timeRange: fmt.formatTimeRange(course.startTime, course.endTime),
    durationText: fmt.durationText(course.startTime, course.endTime),
    remain,
    percent: course.capacity ? Math.min(100, Math.round(((course.booked || 0) / course.capacity) * 100)) : 0,
    display,
    displayText: C.COURSE_DISPLAY_TEXT[display],
    displayClass: C.COURSE_DISPLAY_CLASS[display]
  })
}

function decorateList(list) {
  return (list || []).map(decorate)
}

module.exports = { decorate, decorateList }
