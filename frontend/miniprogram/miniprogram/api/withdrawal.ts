import { http } from '../utils/request'
import type {
  MerchantTransferConfirmationView,
  WithdrawalPage,
  WithdrawalView,
} from '../types/api'

const COLLECTION = '/api/v1/miniapp/me/withdrawals'

export function myWithdrawals(toast = true) {
  return http.get<WithdrawalPage>(
    COLLECTION,
    { limit: 100 },
    { toast, noStore: true },
  )
}

export function createWithdrawal(
  amountYuan: string,
  idempotencyKey: string,
) {
  return http.post<WithdrawalView>(
    COLLECTION,
    { amountYuan },
    { idempotencyKey, noStore: true },
  )
}

export function merchantTransferConfirmation(withdrawalNo: string) {
  return http.get<MerchantTransferConfirmationView>(
    `${COLLECTION}/${encodeURIComponent(withdrawalNo)}`
      + '/merchant-transfer-confirmation',
    undefined,
    { noStore: true },
  )
}
