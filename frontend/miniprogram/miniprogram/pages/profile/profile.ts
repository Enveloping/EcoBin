import { bindCurrentPhone } from '../../api/auth'
import { myWallet } from '../../api/wallet'
import { FEATURES } from '../../config/index'
import { getSession, logout, markPhoneBound } from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'
import {
  dismissUnstartedPendingDeviceEntry,
  routePendingDeviceEntry,
} from '../../utils/device-entry-intent'
import {
  cancelAfterPhoneBindingPrompt,
  continueAfterPhoneBindingPrompt,
  requestPhoneBindingBeforeAction,
} from '../../utils/phone-binding-prompt'
import {
  isWechatPhoneGrantCancelled,
  reportPhoneBindingError,
  reportWechatPhoneGrantError,
  type WechatPhoneGrantDetail,
} from '../../utils/phone-grant'
import {
  ENTRY_PREVIEW_NOTICE,
  isEntryPreviewEnabled,
  showEntryPreviewSwitcher,
} from '../../utils/test-entry-preview'
import { toWalletDisplay } from '../../utils/user-view'

interface MenuItem {
  text: string
  icon: string
  url: string
}

Page({
  phoneBindingIntentKey: '',

  data: {
    nickname: '小程序用户2168',
    organizationName: '当前机构',
    phoneBound: false,
    showPhoneGrant: false,
    phoneGrantSubmitting: false,
    walletDataAvailable: FEATURES.targetWalletApi,
    availableBalanceText: '—',
    pendingRewardText: '—',
    withdrawalProcessingText: '—',
    walletLoading: FEATURES.targetWalletApi,
    walletError: false,
    hasAvailableBalance: false,
    hasWithdrawalProcessing: false,
    withdrawalProcessingKnown: false,
    canWithdraw: false,
    entryPreviewEnabled: false,
    entryPreviewNotice: ENTRY_PREVIEW_NOTICE,
    shortcuts: [
      { text: '投递订单', icon: 'root-list', url: '/pages/orders/orders' },
      { text: '钱包明细', icon: 'wallet', url: '/pages/wallet/wallet' },
      { text: '提现记录', icon: 'time', url: '/pages/withdrawals/withdrawals' },
    ] as MenuItem[],
    helpers: [
      { text: '常见问题', icon: 'info-circle', url: '/pages/placeholder/placeholder?title=常见问题' },
      { text: '联系客服', icon: 'service', url: '/pages/customer-service/customer-service' },
      { text: '隐私与设置', icon: 'setting', url: '/pages/placeholder/placeholder?title=隐私与设置' },
    ] as MenuItem[],
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
    const session = getSession()
    if (session) {
      this.setData({
        nickname: session.displayName,
        organizationName: session.organization.displayName,
        phoneBound: session.phoneBound,
        entryPreviewEnabled: isEntryPreviewEnabled(),
      }, () => {
        this.setPhoneGrantTabBarHidden(this.data.showPhoneGrant)
        if (FEATURES.targetWalletApi) void this.loadWallet()
      })
      return
    }

    this.setData({
      phoneBound: false,
      showPhoneGrant: false,
      entryPreviewEnabled: isEntryPreviewEnabled(),
    })
    this.setPhoneGrantTabBarHidden(false)
  },

  async loadWallet() {
    if (!FEATURES.targetWalletApi) {
      this.setData({ walletLoading: false, walletError: false })
      return
    }
    this.setData({ walletLoading: true, walletError: false })
    try {
      const wallet = toWalletDisplay(await myWallet(false))
      this.setData({
        availableBalanceText: wallet.availableBalance === '—'
          ? '—'
          : `¥${wallet.availableBalance}`,
        pendingRewardText: wallet.pendingReward === '—'
          ? '—'
          : `¥${wallet.pendingReward}`,
        withdrawalProcessingText: wallet.withdrawalProcessing === '—'
          ? '—'
          : `¥${wallet.withdrawalProcessing}`,
        hasAvailableBalance: wallet.hasAvailableBalance,
        hasWithdrawalProcessing: wallet.hasWithdrawalProcessing,
        withdrawalProcessingKnown: wallet.withdrawalProcessingKnown,
        canWithdraw: wallet.canWithdraw,
      })
    } catch (error) {
      this.setData({
        availableBalanceText: '—',
        pendingRewardText: '—',
        withdrawalProcessingText: '—',
        hasAvailableBalance: false,
        hasWithdrawalProcessing: false,
        withdrawalProcessingKnown: false,
        canWithdraw: false,
        walletError: true,
      })
    } finally {
      this.setData({ walletLoading: false })
    }
  },

  onWalletRetry() {
    if (!FEATURES.targetWalletApi) return
    void this.loadWallet()
  },

  onMenuTap(e: WechatMiniprogram.TouchEvent) {
    const url = String(e.currentTarget.dataset.url || '')
    if (url) wx.navigateTo({ url })
  },

  onEntryPreview() {
    showEntryPreviewSwitcher()
  },

  onWithdraw() {
    if (!FEATURES.targetWithdrawalApi) {
      wx.showToast({ title: '提现服务正在接入', icon: 'none' })
      return
    }
    if (
      requestPhoneBindingBeforeAction(
        getSession(),
        () => this.continueWithdraw(),
      )
    ) {
      return
    }
    this.continueWithdraw()
  },

  continueWithdraw() {
    if (!FEATURES.targetWithdrawalApi) {
      wx.showToast({ title: '提现服务正在接入', icon: 'none' })
      return
    }
    if (this.data.walletLoading) return
    if (this.data.walletError) {
      wx.showToast({
        title: '余额暂不可用',
        icon: 'none',
      })
      return
    }
    if (!this.data.withdrawalProcessingKnown) {
      wx.showToast({ title: '提现状态暂不可用', icon: 'none' })
      return
    }
    if (this.data.hasWithdrawalProcessing) {
      wx.showToast({ title: '已有提现处理中', icon: 'none' })
      return
    }
    if (!this.data.hasAvailableBalance) {
      wx.showToast({ title: '当前无可提现余额', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/withdrawals/withdrawals' })
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
      this.setData({
        phoneBound: true,
        showPhoneGrant: false,
        canWithdraw: FEATURES.targetWithdrawalApi
          && !this.data.walletLoading
          && !this.data.walletError
          && this.data.hasAvailableBalance
          && this.data.withdrawalProcessingKnown
          && !this.data.hasWithdrawalProcessing,
      }, () => {
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
