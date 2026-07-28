import { startDoorEntry } from '../../utils/door-entry'
import { getSession } from '../../utils/auth'

interface FeatureItem {
  text: string
  icon: string
  url?: string
  scan?: boolean
}

Page({
  phoneGrantDismissed: false,

  data: {
    city: '湖州',
    phoneBound: false,
    showPhoneGrant: false,
    organizationName: '',
    features: [
      { text: '附近设备', icon: 'location', url: '/pages/nearby/nearby' },
      { text: '优选商城', icon: 'cart', url: '/pages/mall/mall' },
      { text: '上门回收', icon: 'vehicle', url: '/pages/pickup/pickup' },
      { text: '联系客服', icon: 'service', url: '/pages/customer-service/customer-service' },
    ] as FeatureItem[],
    categories: ['废纸', '金属', '塑料', '旧衣', '家电'],
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
    const session = getSession()
    if (session) {
      this.setData({
        phoneBound: session.phoneBound,
        showPhoneGrant:
          session.audience === 'miniapp'
          && !session.phoneBound
          && !this.phoneGrantDismissed,
        organizationName: session.organization.displayName,
      })
    }
  },

  onScan() {
    startDoorEntry()
  },

  onBindPhone() {
    this.setData({ showPhoneGrant: true })
  },

  onPhoneGrantClose() {
    this.phoneGrantDismissed = true
    this.setData({ showPhoneGrant: false })
  },

  onPhoneBound() {
    this.setData({ phoneBound: true, showPhoneGrant: false })
  },

  onFeatureTap(e: WechatMiniprogram.TouchEvent) {
    const item = this.data.features[Number(e.currentTarget.dataset.index)]
    if (!item) return
    if (item.scan) startDoorEntry()
    else if (item.url) wx.navigateTo({ url: item.url })
  },

  onCity() {
    wx.showActionSheet({
      itemList: ['湖州', '杭州', '嘉兴'],
      success: (res) => this.setData({ city: ['湖州', '杭州', '嘉兴'][res.tapIndex] }),
    })
  },

  onNearby() {
    wx.navigateTo({ url: '/pages/nearby/nearby' })
  },

  onWithdraw() {
    wx.navigateTo({ url: '/pages/placeholder/placeholder?title=账户提现' })
  },
})
