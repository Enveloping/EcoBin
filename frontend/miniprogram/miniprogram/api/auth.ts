import { http } from '../utils/request'
import type { LoginResponse } from '../types/api'

/** 微信登录：code + 本小程序 appid 换取 token，无需鉴权头 */
export function wxLogin(code: string, appid: string) {
  return http.post<LoginResponse>('/api/system/auth/wx-login', { code, appid }, { auth: false })
}
