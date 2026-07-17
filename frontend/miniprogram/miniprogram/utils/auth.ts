/**
 * 登录态管理：微信静默登录、token / role / 用户信息读写。
 */
import { wxLogin } from '../api/auth'
import { STORAGE_KEYS } from '../config/index'
import type { LoginResponse } from '../types/api'

/** wx.login 取 code（Promise 化） */
function getLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (res) => (res.code ? resolve(res.code) : reject(new Error('wx.login 未返回 code'))),
      fail: reject,
    })
  })
}

/** 取本小程序自身 AppID（用于后端定位租户） */
function getAppId(): string {
  return wx.getAccountInfoSync().miniProgram.appId
}

/** 把登录结果写入 storage + globalData */
function persist(resp: LoginResponse) {
  wx.setStorageSync(STORAGE_KEYS.token, resp.token)
  wx.setStorageSync(STORAGE_KEYS.role, resp.role)
  wx.setStorageSync(STORAGE_KEYS.userInfo, resp)
  const app = getApp<IAppOption>()
  if (app) {
    app.globalData.token = resp.token
    app.globalData.role = resp.role
    app.globalData.userInfo = resp
  }
}

/** 执行微信登录：code + appid 换 token，落地登录态 */
export async function login(): Promise<LoginResponse> {
  const code = await getLoginCode()
  const appid = getAppId()
  const resp = await wxLogin(code, appid)
  persist(resp)
  return resp
}

/** 进行中的刷新 promise，用于并发去重 */
let refreshing: Promise<LoginResponse> | null = null

/**
 * 静默刷新 token（并发去重）：token 过期时自动重新 wx.login 换新 token。
 * 多处同时调用（如多个并发请求一起 401）共享同一次 wx.login，避免重复登录。
 */
export function refreshToken(): Promise<LoginResponse> {
  if (!refreshing) {
    refreshing = login().finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

/** 是否已登录 */
export function isLoggedIn(): boolean {
  return !!wx.getStorageSync(STORAGE_KEYS.token)
}

/**
 * 判断 JWT 是否已过期（或临近过期）。
 * 仅用于决定「要不要主动刷新」，不作为鉴权依据——真正鉴权始终由后端完成。
 * 解析失败一律视为已过期以触发刷新（安全降级）。
 * @param token   JWT 字符串
 * @param skewMs  提前量，默认提前 60s 视为过期，抵消客户端时钟漂移与网络耗时
 */
export function isTokenExpired(token: string, skewMs = 60000): boolean {
  try {
    const payload = token.split('.')[1]
    if (!payload) return true
    // base64url -> base64，并补齐 padding
    let b64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    while (b64.length % 4) b64 += '='
    const bytes = new Uint8Array(wx.base64ToArrayBuffer(b64))
    let str = ''
    for (let i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i])
    const claims = JSON.parse(str) as { exp?: number }
    if (!claims.exp) return true
    return Date.now() >= claims.exp * 1000 - skewMs
  } catch (e) {
    return true
  }
}

/** 当前角色（优先内存，回退 storage） */
export function getRole(): number | undefined {
  const app = getApp<IAppOption>()
  if (app && app.globalData.role) return app.globalData.role
  const role = wx.getStorageSync(STORAGE_KEYS.role)
  return role || undefined
}

/** 当前用户信息 */
export function getUserInfo(): LoginResponse | undefined {
  const app = getApp<IAppOption>()
  if (app && app.globalData.userInfo) return app.globalData.userInfo
  const info = wx.getStorageSync(STORAGE_KEYS.userInfo)
  return info || undefined
}

/** 退出登录：清登录态并回登录页 */
export function logout() {
  wx.removeStorageSync(STORAGE_KEYS.token)
  wx.removeStorageSync(STORAGE_KEYS.role)
  wx.removeStorageSync(STORAGE_KEYS.userInfo)
  const app = getApp<IAppOption>()
  if (app) {
    app.globalData.token = undefined
    app.globalData.role = undefined
    app.globalData.userInfo = undefined
  }
  wx.reLaunch({ url: '/pages/login/login' })
}
