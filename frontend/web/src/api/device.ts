import request from './request';
import type { Device, PageParams, PageResult } from '@/types';

export function pageDevices(params: PageParams) {
  return request<PageResult<Device>>({ url: '/device', method: 'GET', params });
}

export function getDevice(id: number) {
  return request<Device>({ url: `/device/${id}`, method: 'GET' });
}

export function createDevice(data: Device) {
  return request<unknown>({ url: '/device', method: 'POST', data });
}

export function updateDevice(id: number, data: Device) {
  return request<unknown>({ url: `/device/${id}`, method: 'PUT', data });
}

export function deleteDevice(id: number) {
  return request<unknown>({ url: `/device/${id}`, method: 'DELETE' });
}
