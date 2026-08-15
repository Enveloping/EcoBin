import { BASE_URL, STORAGE_KEYS, TIMEOUT } from '../config/index'
import { rememberFactoryBinding } from '../utils/factory-mode'

interface Envelope<T> {
  code: 'OK'
  data: T
  requestId: string
}

interface ProblemDetail {
  code?: string
  message?: string
  requestId?: string
}

export interface FactorySession {
  accessToken: string
  tokenType: 'Bearer'
  audience: 'miniapp-factory'
  entryMode: 'FACTORY_ACCEPTANCE'
  expiresAt: string
  factoryOperatorUid: string
  operatorCode: string
  displayName: string
  capabilities: string[]
  newlyBound: boolean
}

interface FactorySessionView {
  audience: 'miniapp-factory'
  entryMode: 'FACTORY_ACCEPTANCE'
  expiresAt: string
  factoryOperatorUid: string
  operatorCode: string
  displayName: string
  capabilities: string[]
}

export interface FactoryBagSlot {
  portNo: number
  bagCode: string
  installedAt: string
}

export interface FactoryAcceptance {
  deviceCode: string
  hardwareSn: string
  expectedPortCount: number
  acceptanceStatus: 'PENDING' | 'FAILED' | 'PASSED'
  allFactoryBagsInstalled: boolean
  acceptanceCanStart: boolean
  factoryBags: FactoryBagSlot[]
}

export class FactoryApiProblem extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string

  constructor(status: number, problem: ProblemDetail) {
    super(problem.message || (status === 0 ? '网络异常，请稍后重试' : '请求失败'))
    this.name = 'FactoryApiProblem'
    this.status = status
    this.code = problem.code || 'COMMON.INVALID_RESPONSE'
    this.requestId = problem.requestId || ''
  }
}

function request<T>(options: {
  url: string
  method?: 'GET' | 'POST' | 'DELETE'
  data?: Record<string, unknown>
  token?: string
  idempotencyKey?: string
}): Promise<T> {
  return new Promise((resolve, reject) => {
    const header: Record<string, string> = {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
      Pragma: 'no-cache',
    }
    if (options.token) header.Authorization = `Bearer ${options.token}`
    if (options.idempotencyKey) {
      header['Idempotency-Key'] = options.idempotencyKey
    }
    wx.request({
      url: BASE_URL + options.url,
      method: options.method || 'GET',
      data: options.data,
      header,
      timeout: TIMEOUT,
      success: (response) => {
        if (response.statusCode === 204) {
          resolve(undefined as T)
          return
        }
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new FactoryApiProblem(
            response.statusCode,
            (response.data || {}) as ProblemDetail,
          ))
          return
        }
        const envelope = response.data as Envelope<T>
        if (!envelope || envelope.code !== 'OK') {
          reject(new FactoryApiProblem(response.statusCode, {}))
          return
        }
        resolve(envelope.data)
      },
      fail: (error) => reject(new FactoryApiProblem(0, {
        code: 'COMMON.NETWORK_ERROR',
        message: error.errMsg,
      })),
    })
  })
}

function loginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (result) => result.code
        ? resolve(result.code)
        : reject(new Error('wx.login 未返回 code')),
      fail: reject,
    })
  })
}

function persist(session: FactorySession): FactorySession {
  // newlyBound 只用于完成绑定后的即时反馈，不能在冷启动时重复展示。
  try {
    wx.setStorageSync(STORAGE_KEYS.factorySession, {
      ...session,
      newlyBound: false,
    })
  } catch {
    // 当前页面仍持有会话；存储恢复后可重新登录。
  }
  rememberFactoryBinding()
  return session
}

export function clearFactorySession(): void {
  try {
    wx.removeStorageSync(STORAGE_KEYS.factorySession)
  } catch {
    try {
      wx.setStorageSync(STORAGE_KEYS.factorySession, null)
    } catch {
      // 服务端会话仍有短有效期，后续请求还会重新校验。
    }
  }
}

export function getFactorySession(): FactorySession | undefined {
  let value: unknown
  try {
    value = wx.getStorageSync(STORAGE_KEYS.factorySession)
  } catch {
    return undefined
  }
  if (!value || typeof value !== 'object') return undefined
  const session = value as FactorySession
  if (
    session.audience !== 'miniapp-factory'
    || !session.accessToken
    || !session.capabilities?.includes('factory.acceptance.read')
    || Date.now() >= Date.parse(session.expiresAt) - 60000
  ) {
    clearFactorySession()
    return undefined
  }
  return session
}

export async function loginFactory(
  bindingToken?: string,
): Promise<FactorySession> {
  const session = await request<FactorySession>({
    url: '/api/v1/miniapp-factory/auth/sessions',
    method: 'POST',
    data: {
      appId: wx.getAccountInfoSync().miniProgram.appId,
      wxLoginCode: await loginCode(),
      bindingToken: bindingToken || null,
    },
  })
  return persist(session)
}

export async function ensureFactorySession(): Promise<FactorySession> {
  const current = getFactorySession()
  if (!current) return loginFactory()
  try {
    const view = await request<FactorySessionView>({
      url: '/api/v1/miniapp-factory/auth/sessions/current',
      token: current.accessToken,
    })
    return persist({
      ...current,
      ...view,
      newlyBound: false,
    })
  } catch (error) {
    if (!(error instanceof FactoryApiProblem) || error.status !== 401) {
      throw error
    }
    clearFactorySession()
    return loginFactory()
  }
}

export async function logoutFactory(): Promise<void> {
  const session = getFactorySession()
  try {
    if (session) {
      await request<void>({
        url: '/api/v1/miniapp-factory/auth/sessions/current',
        method: 'DELETE',
        token: session.accessToken,
      })
    }
  } finally {
    clearFactorySession()
  }
}

async function authorized<T>(options: {
  url: string
  method?: 'GET' | 'POST' | 'DELETE'
  data?: Record<string, unknown>
  idempotencyKey?: string
}): Promise<T> {
  const session = await ensureFactorySession()
  try {
    return await request<T>({ ...options, token: session.accessToken })
  } catch (error) {
    if (error instanceof FactoryApiProblem && error.status === 401) {
      clearFactorySession()
    }
    throw error
  }
}

export function factoryAcceptance(deviceCode: string) {
  return authorized<FactoryAcceptance>({
    url: `/api/v1/miniapp-factory/device-assets/${encodeURIComponent(deviceCode)}`,
  })
}

export function installFactoryBag(
  deviceCode: string,
  portNo: number,
  bagCode: string,
  idempotencyKey: string,
) {
  return authorized<FactoryAcceptance>({
    url: `/api/v1/miniapp-factory/device-assets/${encodeURIComponent(deviceCode)}/factory-bags`,
    method: 'POST',
    data: { portNo, bagCode },
    idempotencyKey,
  })
}

export function correctFactoryBag(
  deviceCode: string,
  portNo: number,
  bagCode: string,
  reason: string,
  idempotencyKey: string,
) {
  return authorized<FactoryAcceptance>({
    url: `/api/v1/miniapp-factory/device-assets/${encodeURIComponent(deviceCode)}/factory-bags/${portNo}/corrections`,
    method: 'POST',
    data: { bagCode, reason },
    idempotencyKey,
  })
}
