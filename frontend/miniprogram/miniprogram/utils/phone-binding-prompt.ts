import { STORAGE_KEYS } from '../config/index'
import type { LoginResponse } from '../types/api'

interface PhoneGrantHost {
  requestPhoneBinding?: () => void
}

let pendingPhoneBindingPrompt = false
let pendingPhoneAction: (() => void) | undefined
let pendingPhoneCancellation: (() => void) | undefined
let autoPromptedInMemory = false

function isUnboundUserSession(
  session: LoginResponse | undefined,
): session is LoginResponse {
  return !!session
    && session.audience === 'miniapp'
    && session.entryMode === 'USER'
    && !session.phoneBound
}

function markAutoPrompted(): void {
  autoPromptedInMemory = true
  try {
    wx.setStorageSync(STORAGE_KEYS.phoneAutoPrompted, true)
  } catch (error) {
    console.warn('[phone-auth] 无法保存自动提示状态', error)
  }
}

function currentPhoneGrantHost(): PhoneGrantHost | undefined {
  const pages = getCurrentPages()
  return pages[pages.length - 1] as unknown as PhoneGrantHost | undefined
}

/**
 * 新的 wx.login 会话获得一次自动提示机会；缓存会话的页面重建不会重置。
 */
export function resetPhoneBindingAutoPrompt(): void {
  autoPromptedInMemory = false
  pendingPhoneBindingPrompt = false
  pendingPhoneAction = undefined
  pendingPhoneCancellation = undefined
  try {
    wx.removeStorageSync(STORAGE_KEYS.phoneAutoPrompted)
  } catch (error) {
    console.warn('[phone-auth] 无法重置自动提示状态', error)
  }
}

/**
 * 只供普通用户首页 onShow 消费。读取和写入在同一同步调用中完成，
 * 因此同一登录会话即使多次创建页面也只会返回一次 true。
 */
export function consumePhoneBindingAutoPrompt(
  session: LoginResponse | undefined,
): boolean {
  if (!isUnboundUserSession(session) || autoPromptedInMemory) return false

  try {
    if (wx.getStorageSync(STORAGE_KEYS.phoneAutoPrompted) === true) {
      autoPromptedInMemory = true
      return false
    }
  } catch (error) {
    console.warn('[phone-auth] 无法读取自动提示状态', error)
  }

  markAutoPrompted()
  return true
}

function showPhoneBindingPrompt(
  afterPrompt?: () => void,
  afterCancel?: () => void,
): void {
  markAutoPrompted()
  pendingPhoneAction = afterPrompt
  pendingPhoneCancellation = afterCancel
  const host = currentPhoneGrantHost()
  if (typeof host?.requestPhoneBinding === 'function') {
    host.requestPhoneBinding()
    return
  }

  pendingPhoneBindingPrompt = true
  wx.switchTab({
    url: '/pages/home/home',
    fail: () => {
      pendingPhoneBindingPrompt = false
      cancelAfterPhoneBindingPrompt()
      wx.showToast({
        title: '暂时无法打开手机号验证',
        icon: 'none',
      })
    },
  })
}

/**
 * 明确要求手机号的功能入口前置检查。返回 true 表示业务动作已暂停；
 * 只有手机号绑定成功后才能调用 continueAfterPhoneBindingPrompt 继续。
 * 用户关闭或拒绝授权时必须调用 cancelAfterPhoneBindingPrompt 丢弃该动作。
 */
export function requestPhoneBindingBeforeAction(
  session: LoginResponse | undefined,
  action: () => void,
  onCancel?: () => void,
): boolean {
  if (!isUnboundUserSession(session)) return false
  showPhoneBindingPrompt(action, onCancel)
  return true
}

export function continueAfterPhoneBindingPrompt(): boolean {
  const action = pendingPhoneAction
  pendingPhoneAction = undefined
  pendingPhoneCancellation = undefined
  if (!action) return false
  action()
  return true
}

export function cancelAfterPhoneBindingPrompt(): boolean {
  const hadPendingAction = !!pendingPhoneAction
  const cancellation = pendingPhoneCancellation
  pendingPhoneAction = undefined
  pendingPhoneCancellation = undefined
  cancellation?.()
  return hadPendingAction
}

export function consumePendingPhoneBindingPrompt(): boolean {
  const pending = pendingPhoneBindingPrompt
  pendingPhoneBindingPrompt = false
  return pending
}
