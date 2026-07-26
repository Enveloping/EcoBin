import { http } from '../utils/request'
import type {
  LoginResponse,
  MiniappAudience,
  MiniappSessionView,
} from '../types/api'

export interface RegistrationSource {
  deploymentCode: string
}

/** 精确匿名入口：AppID + 一次性 wx.login code，返回且只返回一个 audience。 */
export function wxLogin(
  wxLoginCode: string,
  appId: string,
  registrationSource?: RegistrationSource,
) {
  return http.post<LoginResponse>(
    '/api/v1/miniapp/auth/sessions',
    {
      appId,
      wxLoginCode,
      registrationSource: registrationSource ?? null,
    },
    { auth: false },
  )
}

export function deleteCurrentSession(audience: MiniappAudience) {
  const prefix = audience === 'miniapp-staff'
    ? '/api/v1/miniapp-staff'
    : '/api/v1/miniapp'
  return http.del<void>(`${prefix}/auth/sessions/current`, undefined, {
    retryAfterLogin: false,
    toast: false,
  })
}

/** 当前会话安全投影；契约保证不会再次返回 accessToken。 */
export function getCurrentSession(audience: MiniappAudience) {
  const prefix = audience === 'miniapp-staff'
    ? '/api/v1/miniapp-staff'
    : '/api/v1/miniapp'
  return http.get<MiniappSessionView>(
    `${prefix}/auth/sessions/current`,
    undefined,
    { noStore: true },
  )
}
