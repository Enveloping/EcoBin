import { MiniappApiProblem } from './request'

export interface WechatPhoneGrantDetail {
  code?: string
  errMsg?: string
  errno?: number
}

function showPhoneGrantError(content: string): void {
  wx.showModal({
    title: '微信获取手机号异常',
    content,
    showCancel: false,
    confirmText: '我知道了',
  })
}

export function reportWechatPhoneGrantError(
  detail: WechatPhoneGrantDetail,
): void {
  const errno = detail.errno ?? '未返回'
  const errMsg = detail.errMsg?.trim() || '微信未返回错误说明'

  console.error('[phone-auth] 微信获取手机号异常', {
    source: 'wechat',
    errno,
    errMsg,
  })
  showPhoneGrantError(`错误码：${errno}\n错误信息：${errMsg}`)
}

export function reportPhoneBindingError(error: unknown): void {
  if (error instanceof MiniappApiProblem) {
    const requestId = error.requestId || '未返回'
    console.error('[phone-auth] 微信获取手机号异常', {
      source: 'backend',
      status: error.status,
      code: error.code,
      message: error.message,
      requestId,
    })
    showPhoneGrantError(
      `错误码：${error.code}\n`
      + `错误信息：${error.message}\n`
      + `请求编号：${requestId}`,
    )
    return
  }

  const message = error instanceof Error
    ? error.message
    : '未知错误'
  console.error('[phone-auth] 微信获取手机号异常', {
    source: 'backend',
    code: 'UNKNOWN',
    message,
  })
  showPhoneGrantError(`错误码：UNKNOWN\n错误信息：${message}`)
}
