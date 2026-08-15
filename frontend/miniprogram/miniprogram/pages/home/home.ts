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
  overviewContextKey: '',
  overviewLoadPromise: null as Promise<void> | null,
  overviewLoadPromiseContextKey: '',

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
      const contextKey = session.organizationUserUid
      const shouldAutoLoad = this.overviewContextKey !== contextKey
      if (shouldAutoLoad) this.overviewContextKey = contextKey
      this.setData({
        loggedIn: true,
        phoneBound: session.phoneBound,
        showPhoneGrant,
        organizationName: session.organization.displayName,
        ongoingDelivery,
        ...(shouldAutoLoad ? {
          walletBalanceText: '—',
          walletLoading: false,
          walletError: false,
          recentDelivery: null,
          recentLoading: false,
          recentError: false,
        } : {}),
      })
      this.setPhoneGrantTabBarHidden(showPhoneGrant)
      if (shouldAutoLoad) void this.refreshOverview(contextKey)
      return
    }

    consumePendingPhoneBindingPrompt()
    this.overviewContextKey = ''
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

  refreshOverview(contextKey: string): Promise<void> {
    if (!contextKey) return Promise.resolve()
    if (
      this.overviewLoadPromise
      && this.overviewLoadPromiseContextKey === contextKey
    ) {
      return this.overviewLoadPromise
    }

    const tasks: Promise<void>[] = []
    if (FEATURES.targetWalletApi) {
      tasks.push(this.loadWalletSummary(contextKey))
    }
    if (FEATURES.targetDeliveryOrderApi) {
      tasks.push(this.loadRecentDelivery(contextKey))
    }
    const request = Promise.all(tasks).then(() => undefined)
    this.overviewLoadPromise = request
    this.overviewLoadPromiseContextKey = contextKey
    const clearRequest = () => {
      if (this.overviewLoadPromise === request) {
        this.overviewLoadPromise = null
        this.overviewLoadPromiseContextKey = ''
      }
    }
    void request.then(clearRequest, clearRequest)
    return request
  },

  async loadWalletSummary(contextKey: string) {
    if (this.overviewContextKey !== contextKey) return
    this.setData({ walletLoading: true, walletError: false })
    try {
      const wallet = toWalletDisplay(await myWallet(false))
      if (this.overviewContextKey !== contextKey) return
      this.setData({
        walletBalanceText: wallet.availableBalance === '—'
          ? '—'
          : `¥${wallet.availableBalance}`,
      })
    } catch (error) {
      if (this.overviewContextKey !== contextKey) return
      this.setData({ walletBalanceText: '—', walletError: true })
    } finally {
      if (this.overviewContextKey === contextKey) {
        this.setData({ walletLoading: false })
      }
    }
  },

  async loadRecentDelivery(contextKey: string) {
    if (this.overviewContextKey !== contextKey) return
    this.setData({ recentLoading: true, recentError: false })
    try {
      const result = await myDeliveries({ limit: 1 }, false)
      if (this.overviewContextKey !== contextKey) return
      const latest = result.items[0]
      this.setData({
        recentDelivery: latest ? toDeliveryListItem(latest) : null,
      })
    } catch (error) {
      if (this.overviewContextKey !== contextKey) return
      this.setData({ recentDelivery: null, recentError: true })
    } finally {
      if (this.overviewContextKey === contextKey) {
        this.setData({ recentLoading: false })
      }
    }
  },

  async onPullDownRefresh() {
    try {
      const contextKey = getSession()?.organizationUserUid || ''
      if (!contextKey) return
      if (this.overviewContextKey !== contextKey) this.onShow()
      if (this.overviewContextKey === contextKey) {
        await this.refreshOverview(contextKey)
      }
    } finally {
      wx.stopPullDownRefresh()
    }
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
    wx.navigateTo({ url: '/pages/wallet/wallet' })
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
