import { refreshToken, isTokenExpired } from '../../utils/auth'
import { STORAGE_KEYS } from '../../config/index'

Page({
  data: {
    loading: true,
    error: '',
  },

  onLoad() {
    // 本地判断 token 是否过期（仅作刷新提示，不作鉴权依据）：
    // 没过期直接进主页，零多余请求；无 token 或已过期/临近过期，才静默 wx.login 换新 token。
    const token = wx.getStorageSync(STORAGE_KEYS.token)
    if (token && !isTokenExpired(token)) {
      this.toHome()
      return
    }
    this.doLogin()
  },

  async doLogin() {
    this.setData({ loading: true, error: '' })
    try {
      await refreshToken()
      this.toHome()
    } catch (e) {
      this.setData({ loading: false, error: '登录失败，请重试' })
    }
  },

  toHome() {
    wx.reLaunch({ url: '/pages/home/home' })
  },

  onRetry() {
    this.doLogin()
  },
})
