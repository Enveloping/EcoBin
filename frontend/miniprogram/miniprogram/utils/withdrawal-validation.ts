import {
  compareMoneyCny,
  isMoneyCny,
  isPositiveMoneyCny,
  normalizeMoneyInput,
} from './decimal'

export interface WithdrawalLimits {
  hardLimitYuan: string
  manualMinimumYuan: string
  manualMaximumYuan: string
}

export type WithdrawalAmountError =
  | 'INVALID_AMOUNT'
  | 'ZERO_AMOUNT'
  | 'CONFIGURATION_UNAVAILABLE'
  | 'BELOW_MINIMUM'
  | 'ABOVE_MAXIMUM'
  | 'BALANCE_UNAVAILABLE'
  | 'BALANCE_INSUFFICIENT'

export type WithdrawalAmountValidation =
  | { ok: true; amountYuan: string }
  | { ok: false; error: WithdrawalAmountError; amountYuan: string | null }

/**
 * 客户端只做即时提示；后端仍以加锁后的钱包和配置事实为最终裁决。
 */
export function validateWithdrawalAmount(
  input: string,
  limits: WithdrawalLimits | null,
  availableBalanceYuan: string | null,
): WithdrawalAmountValidation {
  const amountYuan = normalizeMoneyInput(input)
  if (!amountYuan) {
    return { ok: false, error: 'INVALID_AMOUNT', amountYuan: null }
  }
  if (compareMoneyCny(amountYuan, '0.00') <= 0) {
    return { ok: false, error: 'ZERO_AMOUNT', amountYuan }
  }
  if (
    !limits
    || !isPositiveMoneyCny(limits.hardLimitYuan)
    || !isPositiveMoneyCny(limits.manualMinimumYuan)
    || !isPositiveMoneyCny(limits.manualMaximumYuan)
  ) {
    return {
      ok: false,
      error: 'CONFIGURATION_UNAVAILABLE',
      amountYuan,
    }
  }
  if (compareMoneyCny(amountYuan, limits.manualMinimumYuan) < 0) {
    return { ok: false, error: 'BELOW_MINIMUM', amountYuan }
  }
  if (
    compareMoneyCny(amountYuan, limits.manualMaximumYuan) > 0
    || compareMoneyCny(amountYuan, limits.hardLimitYuan) > 0
  ) {
    return { ok: false, error: 'ABOVE_MAXIMUM', amountYuan }
  }
  if (!availableBalanceYuan || !isMoneyCny(availableBalanceYuan)) {
    return { ok: false, error: 'BALANCE_UNAVAILABLE', amountYuan }
  }
  if (compareMoneyCny(amountYuan, availableBalanceYuan) > 0) {
    return { ok: false, error: 'BALANCE_INSUFFICIENT', amountYuan }
  }
  return { ok: true, amountYuan }
}
