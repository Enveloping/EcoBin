import request from './request';
import type { Admin } from '@/types';

/** 管理员列表（后端返回纯数组，非分页） */
export function listAdmins() {
  return request<Admin[]>({ url: '/system/admin', method: 'GET' });
}

export function getAdmin(id: number) {
  return request<Admin>({ url: `/system/admin/${id}`, method: 'GET' });
}

export function createAdmin(data: Admin) {
  return request<unknown>({ url: '/system/admin', method: 'POST', data });
}

export function updateAdmin(id: number, data: Admin) {
  return request<unknown>({ url: `/system/admin/${id}`, method: 'PUT', data });
}

export function deleteAdmin(id: number) {
  return request<unknown>({ url: `/system/admin/${id}`, method: 'DELETE' });
}
