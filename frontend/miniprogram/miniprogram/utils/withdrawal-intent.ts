import { STORAGE_KEYS } from '../config/index'
import { isPositiveMoneyCny } from './decimal'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const MAX_AGE_MS = 24 * 60 * 60 * 1000

export interface PendingWithdrawalIntent {
  idempotencyKey: string
  amountYuan: string
  subjectUid: string
  createdAt: number
}

function discard(): void {
  try {
    wx.removeStorageSync(STORAGE_KEYS.withdrawalCreateIntent)
  } catch {
    // 存储不可用时，本次进程仍可完成请求。
  }
}

export function restorePendingWithdrawalIntent(
  subjectUid: string,
  now = Date.now(),
): PendingWithdrawalIntent | null {
  let value: unknown
  try {
    value = wx.getStorageSync(STORAGE_KEYS.withdrawalCreateIntent) as unknown
  } catch {
    return null
  }
  if (!value || typeof value !== 'object') return null
  const intent = value as Partial<PendingWithdrawalIntent>
  if (
    intent.subjectUid !== subjectUid
    || typeof intent.idempotencyKey !== 'string'
    || !UUID_V4.test(intent.idempotencyKey)
    || typeof intent.amountYuan !== 'string'
    || !isPositiveMoneyCny(intent.amountYuan)
    || typeof intent.createdAt !== 'number'
    || !Number.isFinite(intent.createdAt)
    || intent.createdAt > now
    || now - intent.createdAt > MAX_AGE_MS
  ) {
    discard()
    return null
  }
  return intent as PendingWithdrawalIntent
}

export function rememberPendingWithdrawalIntent(
  intent: PendingWithdrawalIntent,
): void {
  try {
    wx.setStorageSync(STORAGE_KEYS.withdrawalCreateIntent, intent)
  } catch {
    // 请求仍携带当前内存中的幂等键，只有冷启动恢复能力降级。
  }
}

export function clearPendingWithdrawalIntent(): void {
  discard()
}
