export interface WechatPhoneGrantDetail {
  code?: string
  errMsg?: string
  errno?: number
}

export function isWechatPhoneGrantCancelled(
  detail: WechatPhoneGrantDetail,
): boolean {
  if (detail.code?.trim()) return false
  if (
    detail.errno === 1
    || detail.errno === 103
    || detail.errno === 104
  ) {
    return true
  }

  const message = detail.errMsg?.trim().toLowerCase() ?? ''
  if (!message.includes('getphonenumber:fail')) return false
  return /\buser (?:deny|denied)\b|\bcancel(?:ed|led)?\b/.test(message)
}
