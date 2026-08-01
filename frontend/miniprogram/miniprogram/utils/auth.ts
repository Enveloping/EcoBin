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
import { clearPendingDeviceEntry } from './device-entry-intent'
import { resetPhoneBindingAutoPrompt } from './phone-binding-prompt'

let sessionClearedLocally = false

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
  try {
    wx.setStorageSync(STORAGE_KEYS.session, session)
  } catch {
    // 当前进程仍使用 globalData；下次冷启动会重新登录。
  }
  const app = getApp<IAppOption>()
  if (app) app.globalData.session = session
  sessionClearedLocally = false
}

export function markPhoneBound(): void {
  const session = getSession()
  if (!session || session.phoneBound) return
  persistSession({ ...session, phoneBound: true })
}

export function clearSession(): void {
  sessionClearedLocally = true
  try {
    wx.removeStorageSync(STORAGE_KEYS.session)
  } catch {
    try {
      wx.setStorageSync(STORAGE_KEYS.session, null)
    } catch {
      // globalData 仍会在下方清空。
    }
  }
  resetPhoneBindingAutoPrompt()
  const app = getApp<IAppOption>()
  if (app) {
    app.globalData.session = undefined
    app.globalData.testViewMode = undefined
  }
}

export function getSession(): LoginResponse | undefined {
  const app = getApp<IAppOption>()
  if (app?.globalData.session) return app.globalData.session
  if (sessionClearedLocally) return undefined
  let stored: unknown
  try {
    stored = wx.getStorageSync(STORAGE_KEYS.session) as unknown
  } catch {
    return undefined
  }
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

interface ActiveLogin {
  sequence: number
  promise: Promise<LoginResponse>
}

let loginSequence = 0
let activeLogin: ActiveLogin | null = null
let refreshing: Promise<LoginResponse> | null = null

function cancelActiveLogin(): void {
  loginSequence += 1
  activeLogin = null
  refreshing = null
}

export function login(
  registrationSource?: RegistrationSource,
): Promise<LoginResponse> {
  // 注册归因不可变，因此登录请求必须单飞：第一个已经发出的登录意图
  // 决定首次来源，后续热启动/扫码只等待它，不能并发争抢后端创建顺序。
  if (activeLogin?.sequence === loginSequence) {
    return activeLogin.promise
  }

  const sequence = ++loginSequence
  const promise = (async () => {
    const code = await getLoginCode()
    const session = await wxLogin(code, getAppId(), registrationSource)
    if (sequence === loginSequence) {
      persistSession(session)
      resetPhoneBindingAutoPrompt()
    }
    return session
  })()
  activeLogin = { sequence, promise }
  void promise.finally(() => {
    if (activeLogin?.sequence === sequence) activeLogin = null
  }).catch(() => undefined)
  return promise
}

/** P0 没有 Refresh Token；“刷新”始终重新执行一次 wx.login。 */
export function refreshSession(
  registrationSource?: RegistrationSource,
): Promise<LoginResponse> {
  if (!refreshing) {
    let tracked: Promise<LoginResponse>
    tracked = login(registrationSource).finally(() => {
      if (refreshing === tracked) refreshing = null
    })
    refreshing = tracked
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
    const sourcedSession = getSession()
    if (sourcedSession && !isSessionExpired(sourcedSession)) {
      try {
        const current = await getCurrentSession(
          sourcedSession.audience,
          false,
        )
        const validated: LoginResponse = {
          ...sourcedSession,
          ...current,
          organization: { ...current.organization },
          capabilities: [...current.capabilities],
        }
        persistSession(validated)
        return validated
      } catch {
        // 服务端会话已失效时，必须带设备来源重新登录，不能无源刷新。
      }
    }
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
    cancelActiveLogin()
    clearPendingDeviceEntry()
    clearSession()
    wx.reLaunch({ url: '/pages/login/login' })
  }
}
