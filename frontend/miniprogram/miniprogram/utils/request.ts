/**
 * 网络请求封装：基于 wx.request 的 Promise 化工具。
 *
 * 职责：
 *  - 自动拼接 baseURL、注入 Authorization: Bearer <token>
 *  - 按统一响应 Result<T> 解包，成功返回 data，失败 toast 并 reject
 *  - 401 清理登录态并跳登录页
 */
import { BASE_URL, TIMEOUT, STORAGE_KEYS } from '../config/index'
import { refreshToken } from './auth'
import type { Result } from '../types/api'

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'

interface RequestOptions {
  url: string
  method?: Method
  data?: Record<string, any>
  /** 是否需要鉴权头，默认 true */
  auth?: boolean
  /** 失败时是否自动 toast，默认 true */
  toast?: boolean
  /** 内部标记：是否已为 401 静默刷新并重试过一次，防止无限重试 */
  _retried?: boolean
}

/** 跳登录页（避免重复跳转） */
let redirecting = false
function gotoLogin() {
  if (redirecting) return
  redirecting = true
  wx.removeStorageSync(STORAGE_KEYS.token)
  wx.removeStorageSync(STORAGE_KEYS.role)
  wx.removeStorageSync(STORAGE_KEYS.userInfo)
  wx.reLaunch({
    url: '/pages/login/login',
    complete: () => {
      redirecting = false
    },
  })
}

export function request<T>(options: RequestOptions): Promise<T> {
  const { url, method = 'GET', data, auth = true, toast = true, _retried = false } = options

  const header: Record<string, string> = { 'Content-Type': 'application/json' }
  if (auth) {
    const token = wx.getStorageSync(STORAGE_KEYS.token)
    if (token) {
      header['Authorization'] = `Bearer ${token}`
    }
  }

  return new Promise<T>((resolve, reject) => {
    wx.request({
      url: BASE_URL + url,
      method,
      data,
      header,
      timeout: TIMEOUT,
      success: (res) => {
        const statusCode = res.statusCode
        const body = res.data as Result<T>

        // HTTP 401 或业务 code 401：token 过期/失效
        if (statusCode === 401 || (body && body.code === 401)) {
          // 需鉴权且未重试过：静默 wx.login 换新 token，再用新 token 重试一次原请求，用户无感
          if (auth && !_retried) {
            refreshToken()
              .then(() => resolve(request<T>({ ...options, _retried: true })))
              .catch(() => {
                // 静默刷新失败（wx.login 失败 / 用户或租户已被禁用）：清登录态跳登录页
                if (toast) wx.showToast({ title: '登录已失效，请重新登录', icon: 'none' })
                gotoLogin()
                reject(new Error('unauthorized'))
              })
            return
          }
          // 不需鉴权 或 刷新后仍 401：放弃，跳登录页
          if (toast) wx.showToast({ title: '登录已失效，请重新登录', icon: 'none' })
          gotoLogin()
          reject(new Error('unauthorized'))
          return
        }

        if (statusCode < 200 || statusCode >= 300) {
          const msg = (body && body.message) || `请求失败(${statusCode})`
          if (toast) wx.showToast({ title: msg, icon: 'none' })
          reject(new Error(msg))
          return
        }

        // 业务码判断（后端成功为 200）
        if (body && body.code === 200) {
          resolve(body.data)
        } else {
          const msg = (body && body.message) || '请求失败'
          if (toast) wx.showToast({ title: msg, icon: 'none' })
          reject(new Error(msg))
        }
      },
      fail: (err) => {
        if (toast) wx.showToast({ title: '网络异常，请稍后重试', icon: 'none' })
        reject(err)
      },
    })
  })
}

/** 便捷方法 */
export const http = {
  get<T>(url: string, data?: Record<string, any>, opts?: Partial<RequestOptions>) {
    return request<T>({ url, method: 'GET', data, ...opts })
  },
  post<T>(url: string, data?: Record<string, any>, opts?: Partial<RequestOptions>) {
    return request<T>({ url, method: 'POST', data, ...opts })
  },
  put<T>(url: string, data?: Record<string, any>, opts?: Partial<RequestOptions>) {
    return request<T>({ url, method: 'PUT', data, ...opts })
  },
  del<T>(url: string, data?: Record<string, any>, opts?: Partial<RequestOptions>) {
    return request<T>({ url, method: 'DELETE', data, ...opts })
  },
}
