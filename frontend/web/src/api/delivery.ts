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
