import { http } from '../utils/request'
import type {
  CursorPage,
  MiniappWalletEntry,
  MiniappWalletView,
} from '../types/api'

/** 我的钱包余额 */
export function myWallet(toast = true) {
  return http.get<MiniappWalletView>(
    '/api/v1/miniapp/me/wallet',
    undefined,
    { toast, noStore: true },
  )
}

export interface MyWalletEntriesQuery {
  cursor?: string
  limit?: number
}

/** 当前用户可见的钱包流水；提现冻结内部转移由后端在分页前排除。 */
export function myWalletEntries(
  query: MyWalletEntriesQuery = {},
  toast = true,
) {
  const data: Record<string, unknown> = {}
  if (query.cursor) data.cursor = query.cursor
  if (query.limit !== undefined) data.limit = query.limit
  return http.get<CursorPage<MiniappWalletEntry>>(
    '/api/v1/miniapp/me/wallet/entries',
    data,
    { toast, noStore: true },
  )
}
