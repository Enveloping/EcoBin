/**
 * 小程序统一 HTTP 传输：
 * - 只注入当前单一 audience 的 Bearer Token
 * - 错误按 ProblemDetail 映射，HTTP 状态码保持权威
 * - 401 只重登录一次；写请求必须携带原始 Idempotency-Key 才能重放
 */
import { BASE_URL, TIMEOUT } from '../config/index'
import {
  clearSession,
  getAccessToken,
  getSession,
  refreshSession,
  routeToEntry,
} from './auth'
import { sessionEntryChanged } from './session-transition'
import type { ProblemDetail, Result } from '../types/api'

type Method = 'GET' | 'HEAD' | 'OPTIONS' | 'POST' | 'PUT' | 'DELETE'

export interface RequestOptions {
  url: string
  method?: Method
  data?: Record<string, unknown>
  /** 是否需要鉴权头，默认 true。 */
  auth?: boolean
  /** 失败时是否自动 toast，默认 true。 */
  toast?: boolean
  /** 同一用户意图创建一次，所有重试均复用。 */
  idempotencyKey?: string
  /** 查询投影或异步状态时禁止客户端缓存。 */
  noStore?: boolean
  /** 401 后是否允许受控重登录，默认 true。 */
  retryAfterLogin?: boolean
  /** 内部标记：最多重登录并重试一次。 */
  _retried?: boolean
}

interface Execution<T> {
  data: T
  statusCode: number
  headers: WechatMiniprogram.IAnyObject
}

export class MiniappApiProblem extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string
  readonly retryable: boolean
  readonly details: Record<string, unknown>

  constructor(status: number, problem: ProblemDetail) {
    super(problem.message)
    this.name = 'MiniappApiProblem'
    this.status = status
    this.code = problem.code
    this.requestId = problem.requestId
    this.retryable = problem.retryable
    this.details = problem.details
  }

  get isIdempotencyConflict(): boolean {
    return this.code === 'COMMON.IDEMPOTENCY_KEY_CONFLICT'
  }

  get isVersionConflict(): boolean {
    return this.status === 409
      && (this.code.includes('VERSION') || this.code.includes('REVISION'))
  }
}

function isProblemDetail(value: unknown): value is ProblemDetail {
  if (!value || typeof value !== 'object') return false
  const problem = value as Partial<ProblemDetail>
  return typeof problem.code === 'string'
    && typeof problem.message === 'string'
    && typeof problem.requestId === 'string'
    && typeof problem.retryable === 'boolean'
    && !!problem.details
    && typeof problem.details === 'object'
}

function toProblem(status: number, value: unknown): MiniappApiProblem {
  if (value instanceof MiniappApiProblem) return value
  if (isProblemDetail(value)) return new MiniappApiProblem(status, value)
  return new MiniappApiProblem(status, {
    code: status === 0 ? 'COMMON.NETWORK_ERROR' : 'COMMON.INVALID_RESPONSE',
    message: status === 0 ? '网络异常，请稍后重试' : `请求失败(${status})`,
    requestId: '',
    retryable: status === 0 || status >= 500,
    details: {},
  })
}

function isSafeMethod(method: Method): boolean {
  return method === 'GET' || method === 'HEAD' || method === 'OPTIONS'
}

let redirecting = false
function gotoLogin(): void {
  if (redirecting) return
  redirecting = true
  clearSession()
  wx.reLaunch({
    url: '/pages/login/login',
    complete: () => {
      redirecting = false
    },
  })
}

function requestOnce<T>(
  options: RequestOptions,
  headers: Record<string, string>,
): Promise<Execution<T>> {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + options.url,
      method: options.method ?? 'GET',
      data: options.data,
      header: headers,
      timeout: TIMEOUT,
      success: (response) => {
        const statusCode = response.statusCode
        if (statusCode < 200 || statusCode >= 300) {
          reject(toProblem(statusCode, response.data))
          return
        }
        if (statusCode === 204) {
          resolve({
            data: undefined as T,
            statusCode,
            headers: response.header,
          })
          return
        }
        const body = response.data as Result<T>
        if (!body || body.code !== 'OK') {
          reject(toProblem(statusCode, response.data))
          return
        }
        resolve({ data: body.data, statusCode, headers: response.header })
      },
      fail: (error) => {
        reject(new MiniappApiProblem(0, {
          code: 'COMMON.NETWORK_ERROR',
          message: error.errMsg || '网络异常，请稍后重试',
          requestId: '',
          retryable: true,
          details: {},
        }))
      },
    })
  })
}

async function execute<T>(options: RequestOptions): Promise<Execution<T>> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (options.noStore) {
    headers['Cache-Control'] = 'no-store'
    headers.Pragma = 'no-cache'
  }
  if (options.idempotencyKey) {
    headers['Idempotency-Key'] = options.idempotencyKey
  }
  if (options.auth !== false) {
    const token = getAccessToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  try {
    return await requestOnce<T>({ ...options, method }, headers)
  } catch (error) {
    const problem = toProblem(
      error instanceof MiniappApiProblem ? error.status : 0,
      error,
    )
    const mayReplay = isSafeMethod(method) || !!options.idempotencyKey
    if (
      problem.status === 401
      && options.auth !== false
      && options.retryAfterLogin !== false
      && !options._retried
      && mayReplay
    ) {
      const previousSession = getSession()
      try {
        const renewed = await refreshSession()
        if (sessionEntryChanged(previousSession, renewed)) {
          routeToEntry(renewed)
          throw new MiniappApiProblem(401, {
            code: 'SECURITY.SESSION_ENTRY_CHANGED',
            message: '登录入口已变化，已切换到正确入口',
            requestId: problem.requestId,
            retryable: false,
            details: {
              previousAudience: previousSession?.audience,
              previousEntryMode: previousSession?.entryMode,
              currentAudience: renewed.audience,
              currentEntryMode: renewed.entryMode,
            },
          })
        }
        return execute<T>({ ...options, method, _retried: true })
      } catch (renewError) {
        if (
          renewError instanceof MiniappApiProblem
          && renewError.code === 'SECURITY.SESSION_ENTRY_CHANGED'
        ) {
          throw renewError
        }
        gotoLogin()
        throw problem
      }
    }
    if (problem.status === 401) gotoLogin()
    throw problem
  }
}

async function run<T>(options: RequestOptions): Promise<Execution<T>> {
  try {
    return await execute<T>(options)
  } catch (error) {
    const problem = toProblem(
      error instanceof MiniappApiProblem ? error.status : 0,
      error,
    )
    if (options.toast !== false) {
      wx.showToast({ title: problem.message, icon: 'none' })
    }
    throw problem
  }
}

export async function request<T>(options: RequestOptions): Promise<T> {
  return (await run<T>(options)).data
}

/** 要求真实 HTTP 202，并校验 Location 与 body.statusUrl 一致。 */
export async function requestAccepted<T extends { statusUrl: string }>(
  options: RequestOptions,
): Promise<T> {
  const result = await run<T>(options)
  if (result.statusCode !== 202) {
    throw new Error(`Expected HTTP 202, received ${result.statusCode}`)
  }
  const location = result.headers.Location ?? result.headers.location
  if (typeof location === 'string' && location !== result.data.statusUrl) {
    throw new Error('HTTP Location differs from accepted operation statusUrl')
  }
  return result.data
}

export const http = {
  get<T>(
    url: string,
    data?: Record<string, unknown>,
    options?: Partial<RequestOptions>,
  ) {
    return request<T>({ url, method: 'GET', data, ...options })
  },
  post<T>(
    url: string,
    data?: Record<string, unknown>,
    options?: Partial<RequestOptions>,
  ) {
    return request<T>({ url, method: 'POST', data, ...options })
  },
  put<T>(
    url: string,
    data?: Record<string, unknown>,
    options?: Partial<RequestOptions>,
  ) {
    return request<T>({ url, method: 'PUT', data, ...options })
  },
  del<T>(
    url: string,
    data?: Record<string, unknown>,
    options?: Partial<RequestOptions>,
  ) {
    return request<T>({ url, method: 'DELETE', data, ...options })
  },
}
