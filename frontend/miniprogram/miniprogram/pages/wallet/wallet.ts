import { myWallet, applyWithdraw, myWithdraws } from '../../api/wallet'
import type { WithdrawOrder } from '../../types/api'
import {
  compareMoneyCny,
  formatMoneyCny,
  normalizeMoneyInput,
} from '../../utils/decimal'

interface Row extends WithdrawOrder {
  statusText: string
}

const WITHDRAW_STATUS: Record<number, string> = {
  0: '待审核',
  1: '已通过',
  2: '已驳回',
}

Page({
  data: {
    balance: '0.00',
    pendingBalance: '0.00',
    amount: '',
    submitting: false,
    list: [] as Row[],
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
    this.loadWallet()
    this.loadWithdraws()
  },

  async loadWallet() {
    try {
      const w = await myWallet()
      this.setData({
        balance: formatMoneyCny(w.balance),
        pendingBalance: formatMoneyCny(w.pendingBalance),
      })
    } catch (e) {
      /* request 已 toast */
    }
  },

  async loadWithdraws() {
    try {
      const res = await myWithdraws(1, 20)
      this.setData({
        list: res.records.map((o) => ({ ...o, statusText: WITHDRAW_STATUS[o.status ?? 0] || '未知' })),
      })
    } catch (e) {
      /* ignore */
    }
  },

  onAmountInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ amount: e.detail.value })
  },

  async onWithdraw() {
    const amount = normalizeMoneyInput(this.data.amount)
    if (!amount || amount === '0.00') {
      wx.showToast({ title: '请输入正确的提现金额', icon: 'none' })
      return
    }
    if (compareMoneyCny(amount, this.data.balance) > 0) {
      wx.showToast({ title: '余额不足', icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    try {
      await applyWithdraw(amount)
      wx.showToast({ title: '提现申请已提交', icon: 'success' })
      this.setData({ amount: '' })
      this.loadWallet()
      this.loadWithdraws()
    } catch (e) {
      /* request 已 toast */
    } finally {
      this.setData({ submitting: false })
    }
  },
})
