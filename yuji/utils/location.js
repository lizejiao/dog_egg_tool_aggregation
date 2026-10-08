/**
 * 位置与城市工具
 * ------------------------------------------------------------------
 * v1：城市手动选择（本地缓存持久化），预留定位接口
 *     locate() 后续接 wx.getFuzzyLocation + 腾讯位置服务逆地编码
 * ------------------------------------------------------------------
 */
const { DEFAULT_CITY } = require('./cities')

const KEY_CITY = '_selected_city'

/** 当前城市（缓存 → 默认北京） */
function getCity() {
  try {
    return wx.getStorageSync(KEY_CITY) || DEFAULT_CITY
  } catch (e) {
    return DEFAULT_CITY
  }
}

/** 手动设置城市 */
function setCity(city) {
  try {
    wx.setStorageSync(KEY_CITY, city)
  } catch (e) {
    /* 忽略 */
  }
}

/**
 * Haversine 两点距离（km），用于「距您 x.x km」展示与按距离排序
 * @param {number} lat1 纬度1
 * @param {number} lng1 经度1
 * @param {number} lat2 纬度2
 * @param {number} lng2 经度2
 */
function distanceKm(lat1, lng1, lat2, lng2) {
  const rad = Math.PI / 180
  const dLat = (lat2 - lat1) * rad
  const dLng = (lng2 - lng1) * rad
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) * Math.sin(dLng / 2)
  return Math.round(6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)) * 10) / 10
}

/**
 * 给馆列表附加距离信息并排序
 * - 有定位 & 馆有坐标 → 算距离，按距离升序排前
 * - 无坐标馆靠后（保持原顺序）
 * @param {Array} gyms 馆列表
 * @param {{latitude, longitude}|null} myLoc 我的定位（无则 null）
 */
function decorateWithDistance(gyms, myLoc) {
  const list = (gyms || []).map((g) => {
    const hasBoth = myLoc && g.locateEnabled && g.latitude && g.longitude
    return Object.assign({}, g, {
      distanceKm: hasBoth ? distanceKm(myLoc.latitude, myLoc.longitude, g.latitude, g.longitude) : -1
    })
  })
  if (!myLoc) return list
  return list
    .filter((g) => g.distanceKm >= 0)
    .sort((a, b) => a.distanceKm - b.distanceKm)
    .concat(list.filter((g) => g.distanceKm < 0))
}

/**
 * 获取定位（v1 预留：未接入真实定位，返回 null）
 * v2：wx.getFuzzyLocation → 云函数逆地编码 → 城市名
 */
function locate() {
  return Promise.resolve(null)
}

module.exports = {
  getCity,
  setCity,
  distanceKm,
  decorateWithDistance,
  locate
}
