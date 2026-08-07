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

/**
 * 读取“此刻是否允许开始投递”的展示快照。
 * 该结果只用于页面提示；真正创建会话时，后端会在写锁下重新检查全部条件。
 */
export function getDeliveryOptions(deviceCode: string) {
  return http.get<DeliveryOptionsView>(
    `/api/v1/miniapp/devices/${
      encodeURIComponent(deviceCode)
    }/delivery-options`,
    undefined,
    {
      noStore: true,
      toast: false,
      registrationSource: { deviceCode },
    },
  )
}

/**
 * 提交一次物理投递意图。成功返回 202 只表示后端已建立会话并排队下发，
 * 不表示设备已经开门；调用方必须保留幂等键并继续查询会话状态。
 */
export function startDeliverySession(
  deviceCode: string,
  portNo: number,
  idempotencyKey: string,
) {
  return requestAccepted<DeliverySessionAccepted>({
    url: `/api/v1/miniapp/devices/${
      encodeURIComponent(deviceCode)
    }/ports/${portNo}/delivery-sessions`,
    method: 'POST',
    idempotencyKey,
    noStore: true,
    toast: false,
    registrationSource: { deviceCode },
  })
}

/** 查询当前用户拥有的投递会话，用于把设备异步进度投影到小程序页面。 */
export function getDeliverySession(
  sessionUid: string,
  deviceCode: string,
) {
  return http.get<DeliverySessionView>(
    `/api/v1/miniapp/delivery-sessions/${
      encodeURIComponent(sessionUid)
    }`,
    undefined,
    {
      noStore: true,
      toast: false,
      registrationSource: { deviceCode },
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
