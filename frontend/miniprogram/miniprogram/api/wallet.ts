import { http } from '../utils/request'
import type {
  CursorPage,
  MiniappWalletView,
  PersonalWalletEntry,
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

/** 当前用户的不可变钱包流水；游标由后端签名，客户端不得解析或重建。 */
export function myWalletEntries(
  query: MyWalletEntriesQuery = {},
  toast = true,
) {
  const data: Record<string, unknown> = {}
  if (query.cursor) data.cursor = query.cursor
  if (query.limit !== undefined) data.limit = query.limit
  return http.get<CursorPage<PersonalWalletEntry>>(
    '/api/v1/miniapp/me/wallet/entries',
    data,
    { toast, noStore: true },
  )
}
