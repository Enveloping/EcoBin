import { http } from '../utils/request'
import type { MiniappWalletView } from '../types/api'

/** 我的钱包余额 */
export function myWallet(toast = true) {
  return http.get<MiniappWalletView>(
    '/api/v1/miniapp/me/wallet',
    undefined,
    { toast, noStore: true },
  )
}
