import type { DeliveryOrder, WalletVO } from '../types/api'
import {
  compareMoneyCny,
  formatMoneyCny,
  isMoneyCny,
} from './decimal'

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
  id: number
  orderSn: string
  categoryText: string
  categoryIcon: string
  weightText: string
  amountText: string
  amountTone: AmountTone
  statusText: string
  statusTone: DeliveryTone
  timeText: string
  filterStatus: 'PENDING' | 'APPROVED' | 'OTHER'
}

const WASTE_TYPE_1: Record<number, string> = {
  1: '厨余垃圾',
  2: '可回收物',
  3: '有害垃圾',
  4: '其他垃圾',
}

const WASTE_TYPE_2: Record<number, string> = {
  1: '纸类',
  2: '塑料',
  3: '织物',
  4: '金属',
  5: '其他',
}

const WASTE_ICON: Record<number, string> = {
  1: 'file-copy',
  2: 'delete',
  3: 'layers',
  4: 'filter',
  5: 'delete',
}

function formatOptionalMoney(value?: string): string {
  if (!value) return '—'
  return formatMoneyCny(value)
}

function formatWeight(value?: string): string {
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

function formatTime(value: string): string {
  const normalized = value.replace('T', ' ').replace(/Z$/, '')
  const match = /^(\d{4})-(\d{2})-(\d{2})[ ](\d{2}):(\d{2})/.exec(normalized)
  if (!match) return value
  return `${match[2]}-${match[3]} ${match[4]}:${match[5]}`
}

function formatAmount(
  auditStatus: number | null,
  rawValue?: string | null,
  finalValue?: string | null,
): Pick<DeliveryListItem, 'amountText' | 'amountTone'> {
  if (auditStatus === 2) {
    return { amountText: '未入账', amountTone: 'neutral' }
  }
  if (auditStatus !== 0 && auditStatus !== 1) {
    return { amountText: '金额待认定', amountTone: 'neutral' }
  }

  // 审核通过后只有 finalAmountYuan 才能代表真实入账金额。
  // 旧接口仅返回 weight/price 时不在前端使用浮点数推算金额。
  const value = auditStatus === 1 ? finalValue : rawValue
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

  if (auditStatus !== 1) {
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

export function toWalletDisplay(wallet: WalletVO): WalletDisplay {
  const availableRaw = wallet.availableBalanceYuan ?? wallet.balance
  const pendingRewardRaw = wallet.pendingRewardYuan
  const processingRaw =
    wallet.withdrawalProcessingYuan ?? wallet.pendingBalance
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

export function toDeliveryListItem(order: DeliveryOrder): DeliveryListItem {
  const auditStatus = order.auditStatus ?? null
  const deliveryInProgress = order.deliveryStatus === 0
  const categoryText =
    (order.wasteType2 != null ? WASTE_TYPE_2[order.wasteType2] : undefined)
    ?? (order.wasteType1 != null ? WASTE_TYPE_1[order.wasteType1] : undefined)
    ?? '可回收物'
  const categoryIcon =
    (order.wasteType2 != null ? WASTE_ICON[order.wasteType2] : undefined)
    ?? 'delete'
  const status = deliveryInProgress
    ? { statusText: '投递处理中', statusTone: 'pending' as const }
    : auditStatus === 1
      ? { statusText: '审核通过', statusTone: 'approved' as const }
      : auditStatus === 0
        ? { statusText: '待审核', statusTone: 'pending' as const }
        : auditStatus === 2
          ? { statusText: '历史订单未入账', statusTone: 'attention' as const }
          : { statusText: '状态待同步', statusTone: 'attention' as const }
  const filterStatus = deliveryInProgress
    ? 'OTHER' as const
    : auditStatus === 0
      ? 'PENDING' as const
      : auditStatus === 1
        ? 'APPROVED' as const
        : 'OTHER' as const
  return {
    id: order.id,
    orderSn: order.orderSn || `订单 #${order.id}`,
    categoryText,
    categoryIcon,
    weightText: formatWeight(order.weight),
    ...formatAmount(
      deliveryInProgress ? null : auditStatus,
      order.rawAmountYuan,
      order.finalAmountYuan,
    ),
    ...status,
    timeText: formatTime(order.createTime),
    filterStatus,
  }
}

export function filterDeliveries(
  list: DeliveryListItem[],
  filter: DeliveryFilter,
): DeliveryListItem[] {
  if (filter === 'PENDING') {
    return list.filter((item) => item.filterStatus === 'PENDING')
  }
  if (filter === 'APPROVED') {
    return list.filter((item) => item.filterStatus === 'APPROVED')
  }
  return list
}
