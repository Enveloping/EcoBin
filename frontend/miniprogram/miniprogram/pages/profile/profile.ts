import { getSession, logout } from '../../utils/auth'

interface MenuItem {
  text: string
  icon: string
  url: string
}

Page({
  data: {
    nickname: '小程序用户2168',
    serviceItems: [
      { text: '家政保洁', icon: 'houses' },
      { text: '家电维修', icon: 'tools' },
      { text: '家具安装', icon: 'layers' },
      { text: '养生护理', icon: 'heart' },
      { text: '便民服务', icon: 'service' },
      { text: '清洗服务', icon: 'wash' },
      { text: '家电清洗', icon: 'refresh' },
      { text: '管道疏通', icon: 'link' },
    ],
    shortcuts: [
      { text: '订单列表', icon: 'root-list', url: '/pages/orders/orders' },
      { text: '预约记录', icon: 'calendar', url: '/pages/appointments/appointments' },
      { text: '我的地址', icon: 'location', url: '/pages/placeholder/placeholder?title=我的地址' },
    ] as MenuItem[],
    helpers: [
      { text: '故障上报', icon: 'edit-1', url: '/pages/fault-report/fault-report' },
      { text: '客服联系', icon: 'service', url: '/pages/customer-service/customer-service' },
      { text: '加盟合作', icon: 'cooperate', url: '/pages/franchise/franchise' },
    ] as MenuItem[],
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
    const session = getSession()
    if (session) this.setData({ nickname: session.displayName })
  },

  onMenuTap(e: WechatMiniprogram.TouchEvent) {
    const url = String(e.currentTarget.dataset.url || '')
    if (url) wx.navigateTo({ url })
  },

  onServiceTap(e: WechatMiniprogram.TouchEvent) {
    const title = encodeURIComponent(String(e.currentTarget.dataset.title || '便民服务'))
    wx.navigateTo({ url: `/pages/placeholder/placeholder?title=${title}` })
  },

  onWithdraw() {
    wx.navigateTo({ url: '/pages/placeholder/placeholder?title=账户提现' })
  },

  onLogout() {
    wx.showModal({
      title: '提示',
      content: '确定要退出登录吗？',
      success: (res) => {
        if (res.confirm) void logout()
      },
    })
  },
})
