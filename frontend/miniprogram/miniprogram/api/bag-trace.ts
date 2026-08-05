import { http } from '../utils/request'
import type {
  BagTraceDeliveryOrderDetail,
  BagTraceDeliveryOrderPage,
  BagUseCyclePage,
} from '../types/api'

function path(value: string): string {
  return encodeURIComponent(value)
}

export function bagUseCycles(
  bagQr: string,
  cursor?: string,
  limit = 20,
) {
  return http.get<BagUseCyclePage>(
    `/api/v1/miniapp/bags/${path(bagQr)}/use-cycles`,
    { cursor, limit },
    { noStore: true },
  )
}

export function bagCycleOrders(
  bagQr: string,
  cycleUid: string,
  cursor?: string,
  limit = 20,
) {
  return http.get<BagTraceDeliveryOrderPage>(
    `/api/v1/miniapp/bags/${path(bagQr)}/use-cycles/${path(cycleUid)}`
      + '/delivery-orders',
    { cursor, limit },
    { noStore: true },
  )
}

export function bagCycleOrderDetail(
  bagQr: string,
  cycleUid: string,
  deliveryOrderNo: string,
) {
  return http.get<BagTraceDeliveryOrderDetail>(
    `/api/v1/miniapp/bags/${path(bagQr)}/use-cycles/${path(cycleUid)}`
      + `/delivery-orders/${path(deliveryOrderNo)}`,
    undefined,
    { noStore: true },
  )
}
