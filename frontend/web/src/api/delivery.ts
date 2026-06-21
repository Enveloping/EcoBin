import request from './request';
import type { DeliveryOrder, PageParams, PageResult } from '@/types';

export function pageDeliveries(params: PageParams) {
  return request<PageResult<DeliveryOrder>>({
    url: '/business/delivery',
    method: 'GET',
    params,
  });
}

export function getDelivery(id: number) {
  return request<DeliveryOrder>({ url: `/business/delivery/${id}`, method: 'GET' });
}

/** 今日投递概览 */
export function deliveryTodayOverview() {
  return request<Record<string, unknown>>({
    url: '/business/delivery/today-overview',
    method: 'GET',
  });
}

/** 审核投递订单：auditStatus 1-通过（返现入账）/ 2-拒绝 */
export function auditDelivery(id: number, auditStatus: 1 | 2, remark?: string) {
  return request<unknown>({
    url: `/business/delivery/${id}/audit`,
    method: 'PUT',
    data: { auditStatus, remark },
  });
}
