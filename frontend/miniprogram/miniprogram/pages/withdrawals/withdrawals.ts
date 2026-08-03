import {
  createWithdrawal,
  merchantTransferConfirmation,
  myWithdrawals,
} from '../../api/withdrawal'
import { createIdempotencyKey } from '../../utils/command-intent'
import { formatMoneyCny } from '../../utils/decimal'
import { formatLocalDateTime } from '../../utils/local-time'
import { MiniappApiProblem } from '../../utils/request'
import type { WithdrawalView } from '../../types/api'

interface WithdrawalListItem extends WithdrawalView {
  amountText: string
  createdText: string
  statusText: string
  statusTone: string
}

interface MerchantTransferOptions {
  mchId: string
  appId: string
  package: string
  success: (result: { errMsg: string }) => void
  fail: (result: { errMsg: string }) => void
}

type MerchantTransferWx = typeof wx & {
  requestMerchantTransfer(options: MerchantTransferOptions): void
}

const STATUS: Record<string, [string, string]> = {
  PENDING_REVIEW: ['等待人工审核', 'warning'],
  READY_TO_SUBMIT: ['审核通过，准备转账', 'processing'],
  CHANNEL_PROCESSING: ['微信转账处理中', 'processing'],
  SUCCEEDED: ['提现成功', 'success'],
  REJECTED: ['审核未通过', 'failed'],
  LOCAL_CANCELLED: ['已取消', 'neutral'],
  LOCAL_ABORTED_BEFORE_CHANNEL: ['已终止', 'neutral'],
  CHANNEL_FAILED: ['微信转账失败', 'failed'],
  CHANNEL_CANCELLED: ['微信转账已撤销', 'neutral'],
}

function listItem(item: WithdrawalView): WithdrawalListItem {
  const [statusText, statusTone] = STATUS[item.status]
    ?? [item.status, 'neutral']
  return {
    ...item,
    amountText: formatMoneyCny(item.amountYuan),
    createdText: formatLocalDateTime(item.createdAt),
    statusText,
    statusTone,
  }
}

function errorText(error: unknown): string {
  if (error instanceof MiniappApiProblem) return error.message
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

Page({
  createIntentKey: null as string | null,

  data: {
    amountYuan: '',
    items: [] as WithdrawalListItem[],
    loading: true,
    submitting: false,
    errorMessage: '',
  },

  onLoad() {
    void this.reload()
  },

  onShow() {
    if (!this.data.loading) void this.reload()
  },

  onPullDownRefresh() {
    void this.reload(() => wx.stopPullDownRefresh())
  },

  async reload(done?: () => void) {
    this.setData({ loading: true, errorMessage: '' })
    try {
      const page = await myWithdrawals(false)
      this.setData({ items: page.items.map(listItem) })
    } catch (error) {
      this.setData({ errorMessage: errorText(error) })
    } finally {
      this.setData({ loading: false })
      done?.()
    }
  },

  onAmountInput(event: WechatMiniprogram.Input) {
    this.setData({ amountYuan: String(event.detail.value).trim() })
  },

  async onCreate() {
    if (this.data.submitting) return
    const amount = this.data.amountYuan
    if (!/^(0|[1-9][0-9]*)\.[0-9]{2}$/.test(amount)) {
      wx.showToast({ title: '请输入精确到分的金额', icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    try {
      if (!this.createIntentKey) {
        this.createIntentKey = await createIdempotencyKey()
      }
      await createWithdrawal(amount, this.createIntentKey)
      this.createIntentKey = null
      this.setData({ amountYuan: '' })
      wx.showToast({ title: '提现已提交审核', icon: 'success' })
      await this.reload()
    } catch (error) {
      wx.showToast({ title: errorText(error), icon: 'none' })
      if (!(error instanceof MiniappApiProblem) || !error.retryable) {
        this.createIntentKey = null
      }
    } finally {
      this.setData({ submitting: false })
    }
  },

  async onConfirmTransfer(event: WechatMiniprogram.TouchEvent) {
    const withdrawalNo = String(event.currentTarget.dataset.no)
    if (!wx.canIUse('requestMerchantTransfer')) {
      wx.showModal({
        title: '微信版本过低',
        content: '请更新微信后再确认收款。',
        showCancel: false,
      })
      return
    }
    try {
      const confirmation = await merchantTransferConfirmation(withdrawalNo)
      const merchantWx = wx as MerchantTransferWx
      merchantWx.requestMerchantTransfer({
        mchId: confirmation.mchId,
        appId: confirmation.appId,
        package: confirmation.packageInfo,
        success: () => {
          wx.showToast({
            title: '已返回，正在核对转账结果',
            icon: 'none',
          })
          void this.reload()
        },
        fail: (result) => {
          if (result.errMsg.includes('cancel')) return
          wx.showToast({ title: '收款确认页调起失败', icon: 'none' })
        },
      })
    } catch (error) {
      wx.showToast({ title: errorText(error), icon: 'none' })
    }
  },

  onRetry() {
    void this.reload()
  },
})
