import { http } from '../utils/request'
import type { WalletVO, WithdrawOrder, PageResult } from '../types/api'

/** 我的钱包余额 */
export function myWallet() {
  return http.get<WalletVO>('/api/app/wallet')
}

/** 发起提现 */
export function applyWithdraw(amount: string) {
  return http.post<WithdrawOrder>('/api/app/wallet/withdraw', { amount })
}

/** 我的提现记录分页 */
export function myWithdraws(page = 1, pageSize = 20) {
  return http.get<PageResult<WithdrawOrder>>('/api/app/wallet/withdraw', { page, pageSize })
}
