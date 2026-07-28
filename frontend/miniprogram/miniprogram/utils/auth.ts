import {
  deleteCurrentSession,
  getCurrentSession,
  wxLogin,
  type RegistrationSource,
} from '../api/auth'
import { STORAGE_KEYS } from '../config/index'
import type {
  EntryMode,
  LoginResponse,
  MiniappAudience,
} from '../types/api'

function getLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (result) =>
        result.code
          ? resolve(result.code)
          : reject(new Error('wx.login 未返回 code')),
      fail: reject,
    })
  })
}

function getAppId(): string {
  return wx.getAccountInfoSync().miniProgram.appId
}

function persistSession(session: LoginResponse): void {
  wx.setStorageSync(STORAGE_KEYS.session, session)
  const app = getApp<IAppOption>()
  if (app) app.globalData.session = session
}

export function markPhoneBound(): void {
  const session = getSession()
  if (!session || session.phoneBound) return
  persistSession({ ...session, phoneBound: true })
}

export function clearSession(): void {
  wx.removeStorageSync(STORAGE_KEYS.session)
  const app = getApp<IAppOption>()
  if (app) app.globalData.session = undefined
}

export function getSession(): LoginResponse | undefined {
  const app = getApp<IAppOption>()
  if (app?.globalData.session) return app.globalData.session
  const stored = wx.getStorageSync(STORAGE_KEYS.session) as unknown
  if (!stored || typeof stored !== 'object') return undefined
  const session = stored as LoginResponse
  if (
    session.audience !== 'miniapp'
    && session.audience !== 'miniapp-staff'
  ) {
    clearSession()
    return undefined
  }
  return session
}

export function getAccessToken(): string | undefined {
  return getSession()?.accessToken
}

export function getAudience(): MiniappAudience | undefined {
  return getSession()?.audience
}

export function getEntryMode(): EntryMode | undefined {
  return getSession()?.entryMode
}

export function hasCapability(capability: string): boolean {
  return getSession()?.capabilities.includes(capability) ?? false
}

export function isSessionExpired(
  session: LoginResponse,
  skewMs = 60000,
): boolean {
  const expiresAt = Date.parse(session.expiresAt)
  return !Number.isFinite(expiresAt) || Date.now() >= expiresAt - skewMs
}

export async function login(
  registrationSource?: RegistrationSource,
): Promise<LoginResponse> {
  const code = await getLoginCode()
  const session = await wxLogin(code, getAppId(), registrationSource)
  persistSession(session)
  return session
}

let refreshing: Promise<LoginResponse> | null = null

/** P0 没有 Refresh Token；“刷新”始终重新执行一次 wx.login。 */
export function refreshSession(): Promise<LoginResponse> {
  if (!refreshing) {
    refreshing = login().finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

export async function ensureLoggedIn(
  registrationSource?: RegistrationSource,
): Promise<LoginResponse> {
  if (registrationSource) {
    // Registration attribution is immutable and only accepted by the first
    // source-bearing wx.login. A deleted/expired server session can leave a
    // stale local projection behind; validating it would trigger the generic
    // source-less refresh path before the QR source reaches the backend.
    // Existing server users remain the same user, so later QR scans still
    // cannot backfill or overwrite their original attribution.
    clearSession()
    return login(registrationSource)
  }
  const session = getSession()
  if (session && !isSessionExpired(session)) {
    const current = await getCurrentSession(session.audience)
    const validated: LoginResponse = {
      ...session,
      ...current,
      organization: { ...current.organization },
      capabilities: [...current.capabilities],
    }
    persistSession(validated)
    return validated
  }
  return refreshSession()
}

export function isLoggedIn(): boolean {
  const session = getSession()
  return !!session && !isSessionExpired(session)
}

export function entryUrlFor(entryMode: EntryMode): string {
  switch (entryMode) {
    case 'USER':
      return '/pages/home/home'
    case 'CLEANING':
      return '/pages/clean/clean'
    case 'MANAGEMENT':
      return '/pages/management/management'
  }
}

export function routeToEntry(session = getSession()): void {
  if (!session) {
    wx.reLaunch({ url: '/pages/login/login' })
    return
  }
  wx.reLaunch({ url: entryUrlFor(session.entryMode) })
}

export async function logout(): Promise<void> {
  const session = getSession()
  try {
    if (session) await deleteCurrentSession(session.audience)
  } finally {
    clearSession()
    wx.reLaunch({ url: '/pages/login/login' })
  }
}
