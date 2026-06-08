import request from './request';
import type { Tenant } from '@/types';

/** 租户列表（后端返回纯数组，非分页） */
export function listTenants() {
  return request<Tenant[]>({ url: '/system/tenant', method: 'GET' });
}

export function getTenant(id: number) {
  return request<Tenant>({ url: `/system/tenant/${id}`, method: 'GET' });
}

export function createTenant(data: Tenant) {
  return request<unknown>({ url: '/system/tenant', method: 'POST', data });
}

export function updateTenant(id: number, data: Tenant) {
  return request<unknown>({ url: `/system/tenant/${id}`, method: 'PUT', data });
}

export function deleteTenant(id: number) {
  return request<unknown>({ url: `/system/tenant/${id}`, method: 'DELETE' });
}

/** 租户查自己（仅 role=7） */
export function getMyTenant() {
  return request<Tenant>({ url: '/system/tenant/me', method: 'GET' });
}
