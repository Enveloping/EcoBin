import { http } from '../utils/request'
import { getSession } from '../utils/auth'
import type {
  BagTraceDeliveryOrderDetail,
  BagTraceDeliveryOrderPage,
  BagUseCyclePage,
} from '../types/api'

function path(value: string): string {
  return encodeURIComponent(value)
}

function requireRealCleaningSession(): void {
  const session = getSession()
  if (session?.audience !== 'miniapp' || session.entryMode !== 'CLEANING') {
    throw new Error('当前登录会话不能请求真实清运袋追溯数据')
  }
}

export function bagUseCycles(
  bagQr: string,
  cursor?: string,
  limit = 20,
) {
  requireRealCleaningSession()
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
  requireRealCleaningSession()
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
  requireRealCleaningSession()
  return http.get<BagTraceDeliveryOrderDetail>(
    `/api/v1/miniapp/bags/${path(bagQr)}/use-cycles/${path(cycleUid)}`
      + `/delivery-orders/${path(deliveryOrderNo)}`,
    undefined,
    { noStore: true },
  )
}
