import request from './request';
import type { Door } from '@/types';

export function listDoors(deviceId: number) {
  return request<Door[]>({ url: `/device/door/list/${deviceId}`, method: 'GET' });
}

export function getDoor(id: number) {
  return request<Door>({ url: `/device/door/${id}`, method: 'GET' });
}

export function createDoor(data: Door) {
  return request<unknown>({ url: '/device/door', method: 'POST', data });
}

export function updateDoor(id: number, data: Door) {
  return request<unknown>({ url: `/device/door/${id}`, method: 'PUT', data });
}

export function deleteDoor(id: number) {
  return request<unknown>({ url: `/device/door/${id}`, method: 'DELETE' });
}
