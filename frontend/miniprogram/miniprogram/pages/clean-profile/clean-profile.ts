import { getSession, logout } from '../../utils/auth'
import { requireEntryMode } from '../../utils/guard'

Page({
  data: {
    displayName: '清运人员',
    organizationName: '当前机构',
  },

  onLoad() {
    requireEntryMode(['CLEANING'])
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()

    const session = getSession()
    this.setData({
      displayName: session?.displayName || '清运人员',
      organizationName: session?.organization.displayName || '当前机构',
    })
  },

  onSwitchOrganization() {
    wx.navigateTo({ url: '/pages/account-switcher/account-switcher' })
  },

  onCleanRecords() {
    wx.navigateTo({ url: '/pages/clean-records/clean-records' })
  },

  onLogout() {
    wx.showModal({
      title: '提示',
      content: '确定要退出登录吗？',
      success: ({ confirm }) => {
        if (confirm) void logout()
      },
    })
  },
})
