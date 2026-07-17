import { http } from '../utils/request'
import type { UserProfileVO } from '../types/api'

/** 我的个人信息 */
export function myProfile() {
  return http.get<UserProfileVO>('/api/app/profile')
}
