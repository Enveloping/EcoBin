import { getSession, logout } from '../../utils/auth'

Page({
  data: {
    organizationName: '',
    displayName: '',
    capabilities: [] as string[],
  },

  onLoad() {
    const session = getSession()
    if (!session || session.entryMode !== 'MANAGEMENT') {
      wx.reLaunch({ url: '/pages/login/login' })
      return
    }
    this.setData({
      organizationName: session.organization.displayName,
      displayName: session.displayName,
      capabilities: session.capabilities,
    })
  },

  onLogout() {
    logout()
  },
})
