import { http, requestAccepted } from '../utils/request'
import type {
  CursorPage,
  DeliveryOptionsView,
  DeliveryReviewStatus,
  MiniappDeliveryOrderDetail,
  MiniappDeliveryOrderItem,
  DeliverySessionAccepted,
  DeliverySessionView,
} from '../types/api'

export function getDeliveryOptions(deploymentCode: string) {
  return http.get<DeliveryOptionsView>(
    `/api/v1/miniapp/device-deployments/${
      encodeURIComponent(deploymentCode)
    }/delivery-options`,
    undefined,
    {
      noStore: true,
      toast: false,
      registrationSource: { deploymentCode },
    },
  )
}

export function startDeliverySession(
  deploymentCode: string,
  portNo: number,
  idempotencyKey: string,
) {
  return requestAccepted<DeliverySessionAccepted>({
    url: `/api/v1/miniapp/device-deployments/${
      encodeURIComponent(deploymentCode)
    }/ports/${portNo}/delivery-sessions`,
    method: 'POST',
    idempotencyKey,
    noStore: true,
    toast: false,
    registrationSource: { deploymentCode },
  })
}

export function getDeliverySession(
  sessionUid: string,
  deploymentCode: string,
) {
  return http.get<DeliverySessionView>(
    `/api/v1/miniapp/delivery-sessions/${
      encodeURIComponent(sessionUid)
    }`,
    undefined,
    {
      noStore: true,
      toast: false,
      registrationSource: { deploymentCode },
    },
  )
}

export interface MyDeliveriesQuery {
  cursor?: string
  limit?: number
  reviewStatus?: DeliveryReviewStatus
}

/** 当前用户的投递订单，游标与筛选条件必须成组使用。 */
export function myDeliveries(
  query: MyDeliveriesQuery = {},
  toast = true,
) {
  const data: Record<string, unknown> = {}
  if (query.cursor) data.cursor = query.cursor
  if (query.limit !== undefined) data.limit = query.limit
  if (query.reviewStatus) data.reviewStatus = query.reviewStatus
  return http.get<CursorPage<MiniappDeliveryOrderItem>>(
    '/api/v1/miniapp/me/delivery-orders',
    data,
    { toast, noStore: true },
  )
}

/** 我的单条投递详情 */
export function deliveryDetail(
  deliveryOrderNo: string,
  toast = true,
) {
  return http.get<MiniappDeliveryOrderDetail>(
    `/api/v1/miniapp/me/delivery-orders/${
      encodeURIComponent(deliveryOrderNo)
    }`,
    undefined,
    { toast, noStore: true },
  )
}
