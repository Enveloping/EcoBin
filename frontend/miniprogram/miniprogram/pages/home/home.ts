import { bindCurrentPhone } from '../../api/auth'
import { myDeliveries } from '../../api/delivery'
import { myWallet } from '../../api/wallet'
import { FEATURES } from '../../config/index'
import {
  getSession,
  markPhoneBound,
  requestLoginBeforeAction,
} from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'
import {
  dismissUnstartedPendingDeviceEntry,
  peekPendingDeviceEntry,
  routePendingDeviceEntry,
} from '../../utils/device-entry-intent'
import {
  cancelAfterPhoneBindingPrompt,
  continueAfterPhoneBindingPrompt,
  consumePendingPhoneBindingPrompt,
} from '../../utils/phone-binding-prompt'
import {
  isWechatPhoneGrantCancelled,
  reportPhoneBindingError,
  reportWechatPhoneGrantError,
  type WechatPhoneGrantDetail,
} from '../../utils/phone-grant'
import {
  toDeliveryListItem,
  toWalletDisplay,
  type DeliveryListItem,
} from '../../utils/user-view'

Page({
  phoneBindingIntentKey: '',

  data: {
    loggedIn: false,
    phoneBound: false,
    showPhoneGrant: false,
    phoneGrantSubmitting: false,
    walletDataAvailable: FEATURES.targetWalletApi,
    deliveryOrderDataAvailable: FEATURES.targetDeliveryOrderApi,
    organizationName: '',
    walletBalanceText: '—',
    walletLoading: false,
    walletError: false,
    recentDelivery: null as DeliveryListItem | null,
    recentLoading: false,
    recentError: false,
    ongoingDelivery: false,
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
    const session = getSession()
    if (session) {
      const pendingDeviceEntry = peekPendingDeviceEntry()
      const ongoingDelivery = !!pendingDeviceEntry
        && (!!pendingDeviceEntry.accepted || !!pendingDeviceEntry.idempotencyKey)
      if (
        session.audience === 'miniapp'
        && session.entryMode === 'USER'
        && session.phoneBound
        && pendingDeviceEntry
        && !pendingDeviceEntry.accepted
        && !pendingDeviceEntry.idempotencyKey
        && routePendingDeviceEntry(session)
      ) {
        return
      }
      const pendingPhoneGrant = consumePendingPhoneBindingPrompt()
      const canRequestPhone =
        session.audience === 'miniapp'
        && session.entryMode === 'USER'
        && !session.phoneBound
      const showPhoneGrant = canRequestPhone
        && (
          this.data.showPhoneGrant
          || pendingPhoneGrant
          || !!pendingDeviceEntry
        )
      this.setData({
        loggedIn: true,
        phoneBound: session.phoneBound,
        showPhoneGrant,
        organizationName: session.organization.displayName,
        ongoingDelivery,
      })
      this.setPhoneGrantTabBarHidden(showPhoneGrant)
      this.loadOverview()
      return
    }

    consumePendingPhoneBindingPrompt()
    this.setData({
      loggedIn: false,
      phoneBound: false,
      showPhoneGrant: false,
      ongoingDelivery: false,
      organizationName: '',
      walletBalanceText: '—',
      walletLoading: false,
      walletError: false,
      recentDelivery: null,
      recentLoading: false,
      recentError: false,
    })
    this.setPhoneGrantTabBarHidden(false)
  },

  loadOverview() {
    if (FEATURES.targetWalletApi) void this.loadWalletSummary()
    if (FEATURES.targetDeliveryOrderApi) void this.loadRecentDelivery()
  },

  async loadWalletSummary() {
    this.setData({ walletLoading: true, walletError: false })
    try {
      const wallet = toWalletDisplay(await myWallet(false))
      this.setData({
        walletBalanceText: wallet.availableBalance === '—'
          ? '—'
          : `¥${wallet.availableBalance}`,
      })
    } catch (error) {
      this.setData({ walletBalanceText: '—', walletError: true })
    } finally {
      this.setData({ walletLoading: false })
    }
  },

  async loadRecentDelivery() {
    this.setData({ recentLoading: true, recentError: false })
    try {
      const result = await myDeliveries({ limit: 1 }, false)
      const latest = result.items[0]
      this.setData({
        recentDelivery: latest ? toDeliveryListItem(latest) : null,
      })
    } catch (error) {
      this.setData({ recentDelivery: null, recentError: true })
    } finally {
      this.setData({ recentLoading: false })
    }
  },

  onRetryRecent() {
    if (!FEATURES.targetDeliveryOrderApi) return
    if (this.data.recentError) void this.loadRecentDelivery()
  },

  onOpenProfile() {
    wx.switchTab({ url: '/pages/profile/profile' })
  },

  onWalletTap() {
    if (
      requestLoginBeforeAction(getSession(), () => {
        this.onShow()
        this.onWalletTap()
      })
    ) {
      return
    }
    if (!FEATURES.targetWalletApi) {
      this.onOpenProfile()
      return
    }
    if (this.data.walletError) {
      void this.loadWalletSummary()
      return
    }
    this.onOpenProfile()
  },

  onOpenOrders() {
    if (
      requestLoginBeforeAction(getSession(), () => {
        this.onShow()
        this.onOpenOrders()
      })
    ) {
      return
    }
    wx.navigateTo({ url: '/pages/orders/orders' })
  },

  onOpenRecentOrder() {
    if (
      requestLoginBeforeAction(getSession(), () => {
        this.onShow()
        this.onOpenRecentOrder()
      })
    ) {
      return
    }
    const deliveryOrderNo = this.data.recentDelivery?.deliveryOrderNo
    if (!deliveryOrderNo) {
      this.onOpenOrders()
      return
    }
    wx.navigateTo({
      url: `/pages/order-detail/order-detail?deliveryOrderNo=${
        encodeURIComponent(deliveryOrderNo)
      }`,
    })
  },

  onResumeDelivery() {
    routePendingDeviceEntry(getSession())
  },

  requestPhoneBinding() {
    this.setData({ showPhoneGrant: true })
    this.setPhoneGrantTabBarHidden(true)
  },

  onPhoneGrantClose() {
    if (this.data.phoneGrantSubmitting) return
    this.setData({ showPhoneGrant: false }, () => {
      this.setPhoneGrantTabBarHidden(false)
      if (!cancelAfterPhoneBindingPrompt()) {
        dismissUnstartedPendingDeviceEntry()
      }
    })
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
      if (isWechatPhoneGrantCancelled(event.detail)) {
        this.onPhoneGrantClose()
        return
      }
      reportWechatPhoneGrantError(event.detail)
      return
    }

    await this.ensurePhoneBindingIntent()
    this.setData({ phoneGrantSubmitting: true })
    try {
      await bindCurrentPhone(code, this.phoneBindingIntentKey)
      markPhoneBound()
      this.phoneBindingIntentKey = ''
      this.setData({ phoneBound: true, showPhoneGrant: false }, () => {
        this.setPhoneGrantTabBarHidden(false)
        if (!continueAfterPhoneBindingPrompt()) {
          if (!routePendingDeviceEntry(getSession())) {
            wx.showToast({ title: '手机号已验证', icon: 'success' })
          }
        }
      })
    } catch (error) {
      reportPhoneBindingError(error)
    } finally {
      this.setData({ phoneGrantSubmitting: false })
    }
  },
})
