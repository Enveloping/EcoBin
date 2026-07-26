import { ensureLoggedIn, routeToEntry } from '../../utils/auth'

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
      const session = await ensureLoggedIn()
      routeToEntry(session)
    } catch (e) {
      this.setData({ loading: false, error: '登录失败，请重试' })
    }
  },

  onRetry() {
    this.doLogin()
  },
})
