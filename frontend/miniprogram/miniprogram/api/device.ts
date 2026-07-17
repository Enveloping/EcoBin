import { http } from '../utils/request'
import type { Device, Door, PageResult } from '../types/api'

/** 本租户设备分页列表 */
export function deviceList(page = 1, pageSize = 20) {
  return http.get<PageResult<Device>>('/api/app/device', { page, pageSize })
}

/** 设备详情 */
export function deviceDetail(id: number) {
  return http.get<Device>(`/api/app/device/${id}`)
}

/** 某设备的投口列表 */
export function deviceDoors(deviceId: number) {
  return http.get<Door[]>(`/api/app/device/${deviceId}/doors`)
}
