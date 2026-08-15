import { STORAGE_KEYS } from '../config/index'

const FACTORY_BIND_PAGE = 'factory/pages/bind/bind'
const FACTORY_ACCEPTANCE_PAGE = '/factory/pages/acceptance/acceptance'
const BINDING_TOKEN = /^[A-Za-z0-9_-]{20,128}$/
const BINDING_TOKEN_TTL_MS = 10 * 60 * 1000

interface StoredBindingToken {
  token: string
  capturedAt: number
}

function decoded(value: string): string {
  try {
    return decodeURIComponent(value).trim()
  } catch {
    return value.trim()
  }
}

export function isFactoryPage(path: string | undefined): boolean {
  return !!path && path.startsWith('factory/')
}

export function isFactoryBindingPage(path: string | undefined): boolean {
  return path === FACTORY_BIND_PAGE
}

export function isFactoryModeSuppressed(): boolean {
  try {
    return wx.getStorageSync(STORAGE_KEYS.factoryModeSuppressed) === true
  } catch {
    return false
  }
}

export function isFactoryBindingKnown(): boolean {
  try {
    return wx.getStorageSync(STORAGE_KEYS.factoryBindingKnown) === true
  } catch {
    return false
  }
}

export function rememberFactoryBinding(): void {
  try {
    wx.setStorageSync(STORAGE_KEYS.factoryBindingKnown, true)
  } catch {
    // 仅用于断网时防止误入普通投递；服务端仍是最终身份事实。
  }
}

export function forgetFactoryBinding(): void {
  try {
    wx.removeStorageSync(STORAGE_KEYS.factoryBindingKnown)
  } catch {
    try {
      wx.setStorageSync(STORAGE_KEYS.factoryBindingKnown, false)
    } catch {
      // 下一次成功服务端探测会重新校正。
    }
  }
}

/** 厂家人员主动切去普通身份后，主包内不再显示任何返回厂家端的入口。 */
export function preferOrdinaryMode(): void {
  try {
    wx.setStorageSync(STORAGE_KEYS.factoryModeSuppressed, true)
  } catch {
    // 当前路由仍会立即按普通身份执行；冷启动时重新由后端识别。
  }
}

/** 绑定成功或普通身份明确退出后，恢复“厂家身份优先”的默认选择。 */
export function preferFactoryMode(): void {
  try {
    wx.removeStorageSync(STORAGE_KEYS.factoryModeSuppressed)
  } catch {
    try {
      wx.setStorageSync(STORAGE_KEYS.factoryModeSuppressed, false)
    } catch {
      // 本地存储不可用时，下一次启动仍会向后端探测厂家绑定。
    }
  }
}

/** 捕获官方小程序码的 scene，避免 App 与分包页面生命周期先后差异丢码。 */
export function captureFactoryBindingEntry(
  options: WechatMiniprogram.LaunchOptionsApp,
): string | undefined {
  if (!isFactoryBindingPage(options.path)) return undefined
  const raw = options.query?.scene
  if (typeof raw !== 'string') return undefined
  const token = decoded(raw)
  if (!BINDING_TOKEN.test(token)) return undefined
  try {
    wx.setStorageSync(STORAGE_KEYS.pendingFactoryBindingToken, {
      token,
      capturedAt: Date.now(),
    } satisfies StoredBindingToken)
  } catch {
    // 页面 onLoad 仍能直接读取 options.scene。
  }
  return token
}

export function consumeFactoryBindingToken(
  scene?: string,
): string | undefined {
  const direct = typeof scene === 'string' ? decoded(scene) : ''
  let stored: unknown
  try {
    stored = wx.getStorageSync(STORAGE_KEYS.pendingFactoryBindingToken)
    wx.removeStorageSync(STORAGE_KEYS.pendingFactoryBindingToken)
  } catch {
    stored = undefined
  }
  if (BINDING_TOKEN.test(direct)) return direct
  if (!stored || typeof stored !== 'object') return undefined
  const candidate = stored as StoredBindingToken
  if (
    !BINDING_TOKEN.test(candidate.token)
    || !Number.isFinite(candidate.capturedAt)
    || Date.now() - candidate.capturedAt > BINDING_TOKEN_TTL_MS
  ) {
    return undefined
  }
  return candidate.token
}

export function factoryAcceptanceUrl(deviceCode?: string): string {
  return deviceCode
    ? `${FACTORY_ACCEPTANCE_PAGE}?deviceCode=${encodeURIComponent(deviceCode)}`
    : FACTORY_ACCEPTANCE_PAGE
}

export function routeToFactoryAcceptance(deviceCode?: string): void {
  wx.reLaunch({ url: factoryAcceptanceUrl(deviceCode) })
}
