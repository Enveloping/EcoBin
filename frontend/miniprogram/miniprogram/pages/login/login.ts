import { ensureLoggedIn } from '../../utils/auth'

Page({
  data: {
    loading: true,
    error: '',
  },

  onLoad() {
    this.doLogin()
  },

  async doLogin() {
    this.setData({ loading: true, error: '' })
    try {
      await ensureLoggedIn()
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
