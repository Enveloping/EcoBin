import { http } from '../utils/request'
import type { CleanOrder, PageResult } from '../types/api'

/** 开清运门（扫新空垃圾袋后下发开门指令，毛重/去皮由设备上报；仅清运员/设备管理员） */
export function openClean(doorId: number, bagNo: string) {
  return http.post<void>('/api/app/clean/open', { doorId, bagNo })
}

/** 我的清运记录分页 */
export function myCleans(page = 1, pageSize = 20) {
  return http.get<PageResult<CleanOrder>>('/api/app/clean/my', { page, pageSize })
}

/** 我的单条清运详情 */
export function cleanDetail(id: number) {
  return http.get<CleanOrder>(`/api/app/clean/my/${id}`)
}
