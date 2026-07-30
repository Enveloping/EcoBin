import { http, requestAccepted } from '../utils/request'
import type {
  DeliveryOptionsView,
  DeliveryOrder,
  DeliverySessionAccepted,
  DeliverySessionView,
  PageResult,
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

/** 我的投递记录分页 */
export function myDeliveries(page = 1, pageSize = 20, toast = true) {
  return http.get<PageResult<DeliveryOrder>>(
    '/api/app/delivery/my',
    { page, pageSize },
    { toast },
  )
}

/** 我的单条投递详情 */
export function deliveryDetail(id: number) {
  return http.get<DeliveryOrder>(`/api/app/delivery/my/${id}`)
}
