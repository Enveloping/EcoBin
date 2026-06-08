import request from './request';
import type { CleanOrder, PageParams, PageResult } from '@/types';

export function pageCleans(params: PageParams) {
  return request<PageResult<CleanOrder>>({
    url: '/business/clean',
    method: 'GET',
    params,
  });
}

export function getClean(id: number) {
  return request<CleanOrder>({ url: `/business/clean/${id}`, method: 'GET' });
}

/** 审核清运单：auditStatus 1-通过 / 2-拒绝 */
export function auditClean(id: number, auditStatus: 1 | 2) {
  return request<unknown>({
    url: `/business/clean/${id}/audit`,
    method: 'PUT',
    params: { auditStatus },
  });
}
