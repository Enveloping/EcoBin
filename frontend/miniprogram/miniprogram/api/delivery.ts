import { http } from '../utils/request'
import type { PageResult, DeliveryOrder } from '../types/api'

/**
 * 开投口（激活「当前活跃用户」会话 + 下发开门指令）。
 * 投递改为「上传后建单」：开门不建单，订单在设备投放称重上报后才生成，故无返回体。
 */
export function openDoor(doorId: number, toast = true) {
  return http.post<void>('/api/app/delivery/open', { doorId }, { toast })
}

/** 我的投递记录分页 */
export function myDeliveries(page = 1, pageSize = 20) {
  return http.get<PageResult<DeliveryOrder>>('/api/app/delivery/my', { page, pageSize })
}

/** 我的单条投递详情 */
export function deliveryDetail(id: number) {
  return http.get<DeliveryOrder>(`/api/app/delivery/my/${id}`)
}
