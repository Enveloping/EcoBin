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
import { resetPhoneBindingPromptState } from './phone-binding-prompt'

let sessionClearedLocally = false

function writeSilentLoginSuppressed(suppressed: boolean): void {
  try {
    if (suppressed) {
      wx.setStorageSync(STORAGE_KEYS.silentLoginSuppressed, true)
    } else {
      wx.removeStorageSync(STORAGE_KEYS.silentLoginSuppressed)
    }
  } catch {
    // 本地存储不可用时，当前进程仍可继续作为游客或完成主动登录。
  }
}

export function isSilentLoginSuppressed(): boolean {
  try {
    return wx.getStorageSync(STORAGE_KEYS.silentLoginSuppressed) === true
  } catch {
    return false
  }
}

export function allowSilentLogin(): void {
  writeSilentLoginSuppressed(false)
}

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
  allowSilentLogin()
}

/** 接受后端新签发的单机构会话，例如用户手工切换机构之后。 */
export function adoptSession(session: LoginResponse): void {
  // 手工选择机构是更新的用户意图；任何更早开始的静默登录都不得覆盖它。
  cancelActiveLogin()
  persistSession(session)
  resetPhoneBindingPromptState()
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
  resetPhoneBindingPromptState()
  const app = getApp<IAppOption>()
  if (app) {
    app.globalData.session = undefined
    app.globalData.testViewMode = undefined
  }
}

/** 只清理发出请求时的那一个会话，避免迟到的 401 删除后来登录/切换的会话。 */
export function clearSessionIfCurrent(
  expected: LoginResponse | undefined,
): boolean {
  const current = getSession()
  if (current?.accessToken !== expected?.accessToken) return false
  clearSession()
  return true
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
  sourceDeviceCode?: string
  promise: Promise<LoginResponse>
}

let loginSequence = 0
let activeLogin: ActiveLogin | null = null

function cancelActiveLogin(): void {
  loginSequence += 1
  activeLogin = null
}

export function login(
  registrationSource?: RegistrationSource,
): Promise<LoginResponse> {
  const sourceDeviceCode = registrationSource?.deviceCode
  // 同一来源单飞；新的设备扫码必须优先于正在进行的无来源静默登录，
  // 否则旧请求可能先选中“最近账号”，覆盖扫码明确选择的机构。
  if (
    activeLogin?.sequence === loginSequence
    && activeLogin.sourceDeviceCode === sourceDeviceCode
  ) {
    return activeLogin.promise
  }

  const sequence = ++loginSequence
  const promise = (async () => {
    const code = await getLoginCode()
    const session = await wxLogin(code, getAppId(), registrationSource)
    if (sequence === loginSequence) {
      persistSession(session)
      resetPhoneBindingPromptState()
    }
    return session
  })()
  activeLogin = { sequence, sourceDeviceCode, promise }
  void promise.finally(() => {
    if (activeLogin?.sequence === sequence) activeLogin = null
  }).catch(() => undefined)
  return promise
}

/** P0 没有 Refresh Token；“刷新”始终重新执行一次 wx.login。 */
export function refreshSession(
  registrationSource?: RegistrationSource,
): Promise<LoginResponse> {
  return login(registrationSource)
}

export async function ensureLoggedIn(
  registrationSource?: RegistrationSource,
): Promise<LoginResponse> {
  if (registrationSource) {
    // 扫码是一次明确的机构选择。即使本地已有有效会话，也必须把设备公开码
    // 交给后端重新选择/创建该设备所属机构账号；失败时保留原会话。
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
    wx.reLaunch({ url: '/pages/home/home' })
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
    writeSilentLoginSuppressed(true)
    wx.reLaunch({ url: '/pages/home/home' })
  }
}

function problemCode(error: unknown): string {
  if (!error || typeof error !== 'object') return ''
  const code = (error as { code?: unknown }).code
  return typeof code === 'string' ? code : ''
}

function reportInteractiveLoginFailure(error: unknown): void {
  if (problemCode(error) === 'IDENTITY.DEVICE_REGISTRATION_REQUIRED') {
    wx.showModal({
      title: '请先扫描设备码',
      content: '当前微信尚未注册 EcoBin 账号。请扫描任一已验收设备上的公开二维码，系统会为该设备所属机构创建账号。',
      showCancel: false,
      confirmText: '我知道了',
    })
    return
  }
  const message = error instanceof Error && error.message
    ? error.message
    : '登录失败，请稍后重试'
  wx.showToast({ title: message, icon: 'none' })
}

/**
 * 受保护功能的统一登录门槛。返回 true 表示本次动作已经暂停；用户确认并且
 * 登录成功后才调用 action。游客取消时页面保持原样，不再跳独立登录页。
 */
export function requestLoginBeforeAction(
  session: LoginResponse | undefined,
  action: (authenticated: LoginResponse) => void,
): boolean {
  if (session && !isSessionExpired(session)) return false
  wx.showModal({
    title: '登录后继续',
    content: '该功能需要使用当前微信身份登录。首次使用请先扫描设备上的 EcoBin 二维码完成注册。',
    confirmText: '微信登录',
    cancelText: '暂不登录',
    success: (result) => {
      if (!result.confirm) return
      allowSilentLogin()
      wx.showLoading({ title: '正在登录', mask: true })
      void ensureLoggedIn()
        .then(action)
        .catch(reportInteractiveLoginFailure)
        .finally(() => wx.hideLoading())
    },
  })
  return true
}
