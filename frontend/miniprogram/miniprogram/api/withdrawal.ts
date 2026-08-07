import { http } from '../utils/request'
import type {
  MerchantTransferAuthorizationAcceptedView,
  MerchantTransferAuthorizationView,
  MerchantTransferConfirmationView,
  WithdrawalConfigurationView,
  WithdrawalPage,
  WithdrawalView,
} from '../types/api'

const COLLECTION = '/api/v1/miniapp/me/withdrawals'
const AUTHORIZATION =
  '/api/v1/miniapp/me/merchant-transfer-authorization'

export interface WithdrawalListQuery {
  status?: string
  cursor?: string
  limit?: number
}

export function withdrawalConfiguration(toast = true) {
  return http.get<WithdrawalConfigurationView>(
    '/api/v1/miniapp/me/withdrawal-configuration',
    undefined,
    { toast, noStore: true },
  )
}

export function myWithdrawals(
  query: WithdrawalListQuery = {},
  toast = true,
) {
  const data: Record<string, unknown> = {}
  if (query.status) data.status = query.status
  if (query.cursor) data.cursor = query.cursor
  if (query.limit !== undefined) data.limit = query.limit
  return http.get<WithdrawalPage>(
    COLLECTION,
    data,
    { toast, noStore: true },
  )
}

export function myWithdrawal(withdrawalNo: string, toast = true) {
  return http.get<WithdrawalView>(
    `${COLLECTION}/${encodeURIComponent(withdrawalNo)}`,
    undefined,
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

export function merchantTransferAuthorization(toast = true) {
  return http.get<MerchantTransferAuthorizationView>(
    AUTHORIZATION,
    undefined,
    { toast, noStore: true },
  )
}

export function createMerchantTransferAuthorization(
  idempotencyKey: string,
) {
  return http.post<MerchantTransferAuthorizationAcceptedView>(
    `${AUTHORIZATION}-requests`,
    {},
    { idempotencyKey, noStore: true },
  )
}

export function queryMerchantTransferAuthorization(
  idempotencyKey: string,
) {
  return http.post<MerchantTransferAuthorizationAcceptedView>(
    `${AUTHORIZATION}/queries`,
    {},
    { idempotencyKey, noStore: true },
  )
}
