/**
 * 云函数调用 & 交互反馈 统一封装
 * ------------------------------------------------------------------
 * 云函数统一返回结构：{ code: 0, data: any, message: 'ok' }
 *   code === 0  → 成功，resolve(result.data)
 *   code !== 0  → 业务失败，reject(Error)，message 可直接展示给用户
 * ------------------------------------------------------------------
 */

/**
 * 调用云函数
 * @param {string} name 云函数名
 * @param {object} data 参数
 * @param {object} [options] { loading: true, loadingText: '提交中' }
 */
function call(name, data = {}, options = {}) {
  const { loading = false, loadingText = '加载中' } = options
  if (loading) {
    wx.showLoading({ title: loadingText, mask: true })
  }

  const done = () => {
    if (loading) wx.hideLoading()
  }

  return wx.cloud
    .callFunction({ name, data })
    .then((res) => {
      done()
      const result = res && res.result
      if (!result || typeof result.code === 'undefined') {
        const err = new Error('云函数返回异常，请检查云函数是否已部署')
        err.raw = res
        throw err
      }
      if (result.code !== 0) {
        const err = new Error(result.message || '操作失败')
        err.code = result.code
        err.biz = true // 标记为业务错误（可直接提示用户）
        throw err
      }
      return result.data
    })
    .catch((err) => {
      done()
      // 网络层错误统一转换成人话
      if (!err.biz) {
        err.message = err.message || '网络异常，请稍后重试'
      }
      return Promise.reject(err)
    })
}

/** 轻提示 */
function toast(title, icon = 'none') {
  wx.showToast({ title: String(title).slice(0, 40), icon, duration: 1800 })
}

/** 成功提示 */
function success(title = '操作成功') {
  wx.showToast({ title, icon: 'success', duration: 1500 })
}

/** 失败提示（业务错误直接展示 message） */
function fail(err, fallback = '操作失败') {
  const msg = (err && err.message) || fallback
  wx.showToast({ title: String(msg).slice(0, 40), icon: 'none', duration: 2000 })
}

/** 二次确认弹窗，resolve(true/false) */
function confirm(content, title = '提示', confirmText = '确定') {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      content,
      confirmText,
      confirmColor: '#79927A',
      success: (res) => resolve(!!res.confirm),
      fail: () => resolve(false)
    })
  })
}

/** 提示弹窗，resolve() */
function alert(content, title = '提示') {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      content,
      showCancel: false,
      confirmColor: '#79927A',
      success: () => resolve()
    })
  })
}

/** 输入弹窗，resolve(值 | null) */
function prompt(title, placeholder = '', defaultValue = '') {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      editable: true,
      placeholderText: placeholder,
      content: defaultValue,
      confirmColor: '#79927A',
      success: (res) => resolve(res.confirm ? (res.content || '').trim() : null),
      fail: () => resolve(null)
    })
  })
}

module.exports = {
  call,
  toast,
  success,
  fail,
  confirm,
  alert,
  prompt
}
