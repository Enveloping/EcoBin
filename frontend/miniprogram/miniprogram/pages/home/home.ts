import { startDoorEntry } from '../../utils/door-entry'
import { bindCurrentPhone } from '../../api/auth'
import { getSession, markPhoneBound } from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'

interface FeatureItem {
  text: string
  icon: string
  url?: string
  scan?: boolean
}

Page({
  phoneBindingIntentKey: '',

  data: {
    city: '湖州',
    phoneBound: false,
    phoneGrantSubmitting: false,
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
        organizationName: session.organization.displayName,
      })
    }
  },

  onScan() {
    startDoorEntry()
  },

  async ensurePhoneBindingIntent() {
    if (!this.phoneBindingIntentKey) {
      this.phoneBindingIntentKey = await createIdempotencyKey()
    }
  },

  async onGetPhoneNumber(
    event: WechatMiniprogram.CustomEvent<{
      code?: string
      errMsg?: string
    }>,
  ) {
    if (this.data.phoneGrantSubmitting) return
    const code = event.detail.code
    if (!code) {
      wx.showToast({ title: '已取消手机号授权', icon: 'none' })
      return
    }
    await this.ensurePhoneBindingIntent()
    this.setData({ phoneGrantSubmitting: true })
    try {
      await bindCurrentPhone(code, this.phoneBindingIntentKey)
      markPhoneBound()
      this.setData({ phoneBound: true })
      wx.showToast({ title: '手机号已验证', icon: 'success' })
    } finally {
      this.setData({ phoneGrantSubmitting: false })
    }
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
