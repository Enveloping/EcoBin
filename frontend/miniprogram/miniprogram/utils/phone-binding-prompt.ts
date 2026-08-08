import type { LoginResponse } from '../types/api'

interface PhoneGrantHost {
  requestPhoneBinding?: () => void
}

let pendingPhoneBindingPrompt = false
let pendingPhoneAction: (() => void) | undefined
let pendingPhoneCancellation: (() => void) | undefined

function isUnboundUserSession(
  session: LoginResponse | undefined,
): session is LoginResponse {
  return !!session
    && session.audience === 'miniapp'
    && session.entryMode === 'USER'
    && !session.phoneBound
}

function currentPhoneGrantHost(): PhoneGrantHost | undefined {
  const pages = getCurrentPages()
  return pages[pages.length - 1] as unknown as PhoneGrantHost | undefined
}

/** 清除上一个会话尚未消费的手机号授权界面和受保护操作。 */
export function resetPhoneBindingPromptState(): void {
  pendingPhoneBindingPrompt = false
  pendingPhoneAction = undefined
  pendingPhoneCancellation = undefined
}

function showPhoneBindingPrompt(
  afterPrompt?: () => void,
  afterCancel?: () => void,
): void {
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
