const DEVICE_CODE_PATTERN = /^Dv_[A-Za-z0-9_-]{24,61}$/

/** 设备公开码是唯一允许从二维码进入登录请求的业务参数。 */
export function normalizeDeviceCode(
  value: string | undefined,
): string | undefined {
  const normalized = value?.trim()
  return normalized && DEVICE_CODE_PATTERN.test(normalized)
    ? normalized
    : undefined
}
