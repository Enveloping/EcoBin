import type {
  MiniappDeliveryOrderItem,
  MiniappWalletView,
} from '../types/api'
import {
  compareMoneyCny,
  formatMoneyCny,
  isMoneyCny,
} from './decimal'
import { formatLocalShortDateTime } from './local-time'

export type DeliveryFilter = 'ALL' | 'PENDING' | 'APPROVED'
export type DeliveryTone = 'approved' | 'pending' | 'attention'
export type AmountTone = 'credit' | 'debit' | 'pending' | 'neutral'

export interface WalletDisplay {
  availableBalance: string
  pendingReward: string
  withdrawalProcessing: string
  hasAvailableBalance: boolean
  hasWithdrawalProcessing: boolean
  withdrawalProcessingKnown: boolean
  canWithdraw: boolean
}

export interface DeliveryListItem {
  deliveryOrderNo: string
  categoryText: string
  categoryIcon: string
  weightText: string
  amountText: string
  amountTone: AmountTone
  statusText: string
  statusTone: DeliveryTone
  timeText: string
  filterStatus: 'PENDING' | 'APPROVED'
}

function formatOptionalMoney(value?: string): string {
  if (!value) return '—'
  return formatMoneyCny(value)
}

function formatWeight(value?: string | null): string {
  if (!value) return '重量待认定'
  const match = /^(-?)(0|[1-9]\d*)(?:\.(\d+))?$/.exec(value)
  if (!match) return '重量待认定'
  const fraction = match[3] ?? ''
  const normalizedFraction = fraction.length <= 2
    ? fraction.padEnd(2, '0')
    : fraction.replace(/0+$/, '')
  const decimal = normalizedFraction ? `.${normalizedFraction}` : ''
  return `${match[1]}${match[2]}${decimal}kg`
}

function formatAmount(
  order: MiniappDeliveryOrderItem,
): Pick<DeliveryListItem, 'amountText' | 'amountTone'> {
  const approved = order.reviewStatus === 'APPROVED'
  const value = approved
    ? order.finalAmountYuan
    : order.rawAmountReliability === 'RELIABLE'
      ? order.rawAmountYuan
      : null
  if (!value) {
    return { amountText: '金额待认定', amountTone: 'neutral' }
  }
  const formatted = formatMoneyCny(value)
  if (formatted === '—') {
    return { amountText: '金额待认定', amountTone: 'neutral' }
  }
  const negative = formatted.startsWith('-')
  const absolute = negative ? formatted.slice(1) : formatted
  const zero = absolute === '0.00'

  if (!approved) {
    if (negative) {
      return { amountText: `原始 -¥${absolute}`, amountTone: 'debit' }
    }
    return {
      amountText: `预计 ¥${absolute}`,
      amountTone: zero ? 'neutral' : 'pending',
    }
  }

  if (negative) {
    return { amountText: `-¥${absolute}`, amountTone: 'debit' }
  }
  if (zero) {
    return { amountText: '¥0.00', amountTone: 'neutral' }
  }
  return { amountText: `+¥${absolute}`, amountTone: 'credit' }
}

export function toWalletDisplay(wallet: MiniappWalletView): WalletDisplay {
  const availableRaw = wallet.availableBalanceYuan
  const pendingRewardRaw = wallet.pendingRewardYuan
  const processingRaw = wallet.withdrawalProcessingYuan
  const hasAvailableBalance =
    isMoneyCny(availableRaw)
    && compareMoneyCny(availableRaw, '0.00') > 0
  const withdrawalProcessingKnown = isMoneyCny(processingRaw)
  const hasWithdrawalProcessing =
    withdrawalProcessingKnown
    && compareMoneyCny(processingRaw, '0.00') > 0
  return {
    availableBalance: formatOptionalMoney(availableRaw),
    pendingReward: formatOptionalMoney(pendingRewardRaw),
    withdrawalProcessing: formatOptionalMoney(processingRaw),
    hasAvailableBalance,
    hasWithdrawalProcessing,
    withdrawalProcessingKnown,
    canWithdraw:
      hasAvailableBalance
      && withdrawalProcessingKnown
      && !hasWithdrawalProcessing,
  }
}

export function toDeliveryListItem(
  order: MiniappDeliveryOrderItem,
): DeliveryListItem {
  const approved = order.reviewStatus === 'APPROVED'
  const weight = approved
    ? order.finalWeightKg
    : order.rawWeightReliability === 'RELIABLE'
      ? order.rawWeightKg
      : null
  return {
    deliveryOrderNo: order.deliveryOrderNo,
    categoryText: `${order.portNo} 号投口`,
    categoryIcon: 'delete',
    weightText: formatWeight(weight),
    ...formatAmount(order),
    statusText: approved ? '已审核' : '待审核',
    statusTone: approved ? 'approved' : 'pending',
    timeText: formatLocalShortDateTime(
      order.deviceOccurredAt ?? order.receivedAt,
    ),
    filterStatus: order.reviewStatus,
  }
}
