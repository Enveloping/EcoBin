import { startDoorEntry } from '../../utils/door-entry'
import { bindCurrentPhone } from '../../api/auth'
import { getSession, markPhoneBound } from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'
import {
  reportPhoneBindingError,
  reportWechatPhoneGrantError,
  type WechatPhoneGrantDetail,
} from '../../utils/phone-grant'

interface FeatureItem {
  text: string
  icon: string
  url?: string
  scan?: boolean
}

Page({
  phoneGrantDismissed: false,
  phoneBindingIntentKey: '',

  data: {
    city: '湖州',
    phoneBound: false,
    showPhoneGrant: false,
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
      const showPhoneGrant =
        session.audience === 'miniapp'
        && !session.phoneBound
        && !this.phoneGrantDismissed
      this.setData({
        phoneBound: session.phoneBound,
        showPhoneGrant,
        organizationName: session.organization.displayName,
      })
      this.setPhoneGrantTabBarHidden(showPhoneGrant)
    }
  },

  onScan() {
    startDoorEntry()
  },

  onBindPhone() {
    this.phoneGrantDismissed = false
    this.setData({ showPhoneGrant: true })
    this.setPhoneGrantTabBarHidden(true)
  },

  onPhoneGrantClose() {
    if (this.data.phoneGrantSubmitting) return
    this.phoneGrantDismissed = true
    this.setData({ showPhoneGrant: false })
    this.setPhoneGrantTabBarHidden(false)
  },

  onPhoneSheetPanelTap() {
    // 阻止点击面板内容时触发遮罩关闭。
  },

  async ensurePhoneBindingIntent() {
    if (!this.phoneBindingIntentKey) {
      this.phoneBindingIntentKey = await createIdempotencyKey()
    }
  },

  setPhoneGrantTabBarHidden(hidden: boolean) {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).setHidden?.(hidden)
  },

  async onGetPhoneNumber(
    event: WechatMiniprogram.CustomEvent<WechatPhoneGrantDetail>,
  ) {
    if (this.data.phoneGrantSubmitting) return
    const code = event.detail.code
    if (!code) {
      reportWechatPhoneGrantError(event.detail)
      return
    }
    await this.ensurePhoneBindingIntent()
    this.setData({ phoneGrantSubmitting: true })
    try {
      await bindCurrentPhone(code, this.phoneBindingIntentKey)
      markPhoneBound()
      this.setData({ phoneBound: true, showPhoneGrant: false })
      this.setPhoneGrantTabBarHidden(false)
      wx.showToast({ title: '手机号已验证', icon: 'success' })
    } catch (error) {
      reportPhoneBindingError(error)
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
