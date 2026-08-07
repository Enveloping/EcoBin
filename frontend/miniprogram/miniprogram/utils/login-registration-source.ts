const DEVICE_CODE_PATTERN = /^Dv_[A-Za-z0-9_-]{24,61}$/

export function normalizeDeviceCode(
  value: string | undefined,
): string | undefined {
  const normalized = value?.trim()
  return normalized && DEVICE_CODE_PATTERN.test(normalized)
    ? normalized
    : undefined
}

/**
 * 每一次微信登录（包括用户点击重试）都从保存的设备公开码重新构造注册来源。
 * 返回新对象可以避免登录请求意外修改页面持有的来源状态。
 */
export function loginRegistrationSource(
  deviceCode: string | undefined,
): { deviceCode: string } | undefined {
  const normalized = normalizeDeviceCode(deviceCode)
  return normalized ? { deviceCode: normalized } : undefined
}
