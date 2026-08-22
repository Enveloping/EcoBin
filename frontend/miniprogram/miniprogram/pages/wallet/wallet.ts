import {
  myWallet,
  myWalletEntries,
} from '../../api/wallet'
import {
  formatMoneyCny,
} from '../../utils/decimal'
import { formatLocalDateTime } from '../../utils/local-time'
import { MiniappApiProblem } from '../../utils/request'
import { toWalletDisplay } from '../../utils/user-view'
import { loadVisibleWalletEntryPage } from '../../utils/wallet-entry-visibility'
import type {
  MiniappWalletEntry,
  MiniappWalletEntryType,
  WalletEntrySourceType,
} from '../../types/api'

interface WalletEntryListItem extends MiniappWalletEntry {
  title: string
  sourceText: string
  timeText: string
  availableDeltaText: string
  availableTone: string
  processingDeltaText: string
  processingDeltaPrefixText: string
  processingDeltaClass: string
  processingTone: string
  showAvailableDelta: boolean
  showProcessingDelta: boolean
  availableBalanceAfterText: string
  processingAfterText: string
}

const PAGE_SIZE = 20

const ENTRY_TYPE_TEXT: Record<MiniappWalletEntryType, string> = {
  DELIVERY_INITIAL_REVIEW: '投递返现入账',
  DELIVERY_CORRECTION: '投递返现调整',
  WITHDRAWAL_SUCCEEDED: '提现完成',
  MANUAL_ADJUSTMENT: '账户人工调整',
}

const SOURCE_TYPE_TEXT: Record<WalletEntrySourceType, string> = {
  DELIVERY_ORDER: '投递订单',
  WITHDRAWAL_ORDER: '提现单',
  MANUAL_ADJUSTMENT: '调整单',
}

function isZeroMoney(value: string): boolean {
  return value === '0.00' || value === '-0.00'
}

function signedMoney(value: string): string {
  const formatted = formatMoneyCny(value)
  if (formatted === '—') return '金额异常'
  if (formatted.startsWith('-')) return `-¥${formatted.slice(1)}`
  if (isZeroMoney(value)) return '¥0.00'
  return `+¥${formatted}`
}

function balanceMoney(value: string): string {
  const formatted = formatMoneyCny(value)
  if (formatted === '—') return '—'
  return formatted.startsWith('-')
    ? `-¥${formatted.slice(1)}`
    : `¥${formatted}`
}

function amountTone(value: string): string {
  if (isZeroMoney(value)) return 'amount-neutral'
  return value.startsWith('-') ? 'amount-debit' : 'amount-credit'
}

function walletEntry(item: MiniappWalletEntry): WalletEntryListItem {
  return {
    ...item,
    title: ENTRY_TYPE_TEXT[item.entryType],
    sourceText: `${SOURCE_TYPE_TEXT[item.sourceType]} ${item.sourceNo}`,
    timeText: formatLocalDateTime(item.occurredAt),
    availableDeltaText: signedMoney(item.availableDeltaYuan),
    availableTone: amountTone(item.availableDeltaYuan),
    processingDeltaText: signedMoney(item.processingDeltaYuan),
    processingDeltaPrefixText: item.entryType === 'WITHDRAWAL_SUCCEEDED'
      ? ''
      : '处理中 ',
    processingDeltaClass: item.entryType === 'WITHDRAWAL_SUCCEEDED'
      ? 'entry-amount'
      : 'entry-processing',
    processingTone: amountTone(item.processingDeltaYuan),
    showAvailableDelta: !isZeroMoney(item.availableDeltaYuan),
    showProcessingDelta: !isZeroMoney(item.processingDeltaYuan),
    availableBalanceAfterText: balanceMoney(
      item.availableBalanceAfterYuan,
    ),
    processingAfterText: balanceMoney(
      item.withdrawalProcessingAfterYuan,
    ),
  }
}

Page({
  requestGeneration: 0,
  cursorRecoveryUsed: false,

  data: {
    availableBalance: '—',
    pendingReward: '—',
    withdrawalProcessing: '—',
    summaryLoading: true,
    summaryError: false,
    entries: [] as WalletEntryListItem[],
    nextCursor: null as string | null,
    entriesLoading: false,
    initialLoading: true,
    finished: false,
    entriesError: '',
  },

  onLoad() {
    void this.reload()
  },

  onPullDownRefresh() {
    void this.reload(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    void this.loadMore()
  },

  onUnload() {
    this.requestGeneration += 1
  },

  async reload(
    done?: () => void,
    preserveCursorRecovery = false,
  ) {
    if (!preserveCursorRecovery) this.cursorRecoveryUsed = false
    const generation = ++this.requestGeneration
    this.setData({
      summaryLoading: true,
      summaryError: false,
      entries: [],
      nextCursor: null,
      entriesLoading: false,
      initialLoading: true,
      finished: false,
      entriesError: '',
    })
    try {
      await Promise.all([
        this.loadSummary(generation),
        this.fetchEntries(undefined, generation),
      ])
    } finally {
      done?.()
    }
  },

  async loadSummary(generation: number) {
    try {
      const wallet = toWalletDisplay(await myWallet(false))
      if (generation !== this.requestGeneration) return
      this.setData({
        availableBalance: wallet.availableBalance,
        pendingReward: wallet.pendingReward,
        withdrawalProcessing: wallet.withdrawalProcessing,
      })
    } catch {
      if (generation !== this.requestGeneration) return
      this.setData({ summaryError: true })
    } finally {
      if (generation === this.requestGeneration) {
        this.setData({ summaryLoading: false })
      }
    }
  },

  async loadMore() {
    if (this.data.entriesLoading || this.data.finished) return
    await this.fetchEntries(this.data.nextCursor ?? undefined)
  },

  async fetchEntries(cursor?: string, generation?: number) {
    const activeGeneration = generation ?? this.requestGeneration
    if (
      activeGeneration !== this.requestGeneration
      || this.data.entriesLoading
    ) {
      return
    }
    this.setData({ entriesLoading: true, entriesError: '' })
    let recoverInvalidCursor = false
    try {
      const result = await loadVisibleWalletEntryPage(
        pageCursor => myWalletEntries({
          cursor: pageCursor,
          limit: PAGE_SIZE,
        }, false),
        cursor,
        () => activeGeneration === this.requestGeneration,
      )
      if (activeGeneration !== this.requestGeneration) return
      const rows = result.items.map(walletEntry)
      this.setData({
        entries: cursor ? this.data.entries.concat(rows) : rows,
        nextCursor: result.nextCursor,
        finished: !result.nextCursor,
      })
    } catch (error) {
      if (activeGeneration !== this.requestGeneration) return
      if (
        !this.cursorRecoveryUsed
        && error instanceof MiniappApiProblem
        && error.code === 'COMMON.INVALID_CURSOR'
      ) {
        this.cursorRecoveryUsed = true
        recoverInvalidCursor = true
      } else if (this.data.entries.length === 0) {
        this.setData({ entriesError: '钱包明细暂时无法加载' })
      } else {
        wx.showToast({ title: '更多钱包明细加载失败', icon: 'none' })
      }
    } finally {
      if (activeGeneration === this.requestGeneration) {
        this.setData({
          entriesLoading: false,
          initialLoading: false,
        })
      }
    }

    if (
      recoverInvalidCursor
      && activeGeneration === this.requestGeneration
    ) {
      wx.showToast({ title: '钱包明细已更新', icon: 'none' })
      await this.reload(undefined, true)
    }
  },

  onRetry() {
    void this.reload()
  },

  onContinueLoading() {
    void this.loadMore()
  },

  onWithdrawals() {
    wx.navigateTo({ url: '/pages/withdrawals/withdrawals' })
  },
})
