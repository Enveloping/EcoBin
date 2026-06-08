import request from './request';
import type { PageParams, PageResult, WithdrawOrder } from '@/types';

export function pageWithdraws(params: PageParams & { status?: number }) {
  return request<PageResult<WithdrawOrder>>({
    url: '/system/withdraw',
    method: 'GET',
    params,
  });
}

/** 审核提现单 */
export function auditWithdraw(id: number, pass: boolean, remark?: string) {
  return request<unknown>({
    url: `/system/withdraw/${id}/audit`,
    method: 'POST',
    data: { pass, remark },
  });
}
