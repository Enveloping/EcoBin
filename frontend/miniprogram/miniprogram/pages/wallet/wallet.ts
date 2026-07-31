import { myWallet } from '../../api/wallet'
import { toWalletDisplay } from '../../utils/user-view'

Page({
  data: {
    availableBalance: '—',
    pendingReward: '—',
    withdrawalProcessing: '—',
    loading: false,
    errorMessage: '',
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
    void this.loadWallet()
  },

  async loadWallet() {
    if (this.data.loading) return
    this.setData({ loading: true, errorMessage: '' })
    try {
      const wallet = toWalletDisplay(await myWallet(false))
      this.setData({
        availableBalance: wallet.availableBalance,
        pendingReward: wallet.pendingReward,
        withdrawalProcessing: wallet.withdrawalProcessing,
      })
    } catch (error) {
      this.setData({ errorMessage: '钱包暂时无法加载' })
    } finally {
      this.setData({ loading: false })
    }
  },

  onRetry() {
    void this.loadWallet()
  },
})
