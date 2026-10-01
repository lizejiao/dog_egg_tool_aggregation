/**
 * 日期 / 金额 格式化工具
 * ------------------------------------------------------------------
 * 约定：数据库中所有时间字段统一存「毫秒时间戳(Number)」，
 * 避免 Date 对象在小程序与云函数之间序列化时出现时区偏差。
 * 展示时用这里的函数转成字符串。
 * ------------------------------------------------------------------
 */

function pad(n) {
  return n < 10 ? '0' + n : '' + n
}

/** 把各种输入统一转成 Date */
function toDate(value) {
  if (!value) return null
  if (value instanceof Date) return value
  if (typeof value === 'number') return new Date(value)
  if (typeof value === 'string') {
    // iOS 不支持 '2026-10-01 09:30' 这种格式，需要替换分隔符
    return new Date(value.replace(/-/g, '/').replace('T', ' ').replace(/\.\d+Z?$/, ''))
  }
  return null
}

/** 2026-10-01 */
function formatDate(value) {
  const d = toDate(value)
  if (!d) return ''
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 10-01 */
function formatMonthDay(value) {
  const d = toDate(value)
  if (!d) return ''
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 09:30 */
function formatTime(value) {
  const d = toDate(value)
  if (!d) return ''
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 2026-10-01 09:30 */
function formatDateTime(value) {
  const d = toDate(value)
  if (!d) return ''
  return `${formatDate(d)} ${formatTime(d)}`
}

/** 09:30 - 10:30 */
function formatTimeRange(start, end) {
  const s = formatTime(start)
  const e = formatTime(end)
  if (!s) return ''
  return e ? `${s} - ${e}` : s
}

const WEEK_TEXT = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/** 周三 */
function weekday(value) {
  const d = toDate(value)
  if (!d) return ''
  return WEEK_TEXT[d.getDay()]
}

/** 今天 / 明天 / 周三（用于课程列表的日期块） */
function dayLabel(value) {
  const d = toDate(value)
  if (!d) return ''
  const today = new Date()
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
  const diffDays = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - startOfToday) / 86400000)
  if (diffDays === 0) return '今天'
  if (diffDays === 1) return '明天'
  if (diffDays === -1) return '昨天'
  return WEEK_TEXT[d.getDay()]
}

/** 课程时长：60 分钟 */
function durationText(start, end) {
  const s = toDate(start)
  const e = toDate(end)
  if (!s || !e) return ''
  const min = Math.round((e - s) / 60000)
  return min > 0 ? `${min} 分钟` : ''
}

/** 金额：¥298 */
function currency(n) {
  const num = Number(n || 0)
  return '¥' + (Number.isInteger(num) ? num : num.toFixed(2))
}

/** 距离现在多久（用于公告、记录列表） */
function fromNow(value) {
  const d = toDate(value)
  if (!d) return ''
  const diff = Date.now() - d.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  if (diff < 86400000 * 7) return `${Math.floor(diff / 86400000)} 天前`
  return formatDate(d)
}

/** 生成 YYYY-MM-DD，供「按天筛选」使用 */
function dateKey(value) {
  return formatDate(value)
}

/** 今天往前/往后 n 天，返回 Date */
function shiftDay(n) {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() + n)
  return d
}

module.exports = {
  pad,
  toDate,
  formatDate,
  formatMonthDay,
  formatTime,
  formatDateTime,
  formatTimeRange,
  weekday,
  dayLabel,
  durationText,
  currency,
  fromNow,
  dateKey,
  shiftDay
}
