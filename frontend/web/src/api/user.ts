import request from './request';
import type { PageParams, PageResult, User } from '@/types';

export function pageUsers(params: PageParams) {
  return request<PageResult<User>>({ url: '/system/user', method: 'GET', params });
}

export function getUser(id: number) {
  return request<User>({ url: `/system/user/${id}`, method: 'GET' });
}

/** 改用户角色（仅租户，role 限 1/2/3） */
export function updateUserRole(id: number, role: number) {
  return request<unknown>({
    url: `/system/user/${id}/role`,
    method: 'PUT',
    data: { role },
  });
}
