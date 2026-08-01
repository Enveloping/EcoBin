const DEPLOYMENT_CODE_PATTERN = /^Dp_[A-Za-z0-9_-]{6,61}$/

export function normalizeDeploymentCode(
  value: string | undefined,
): string | undefined {
  const normalized = value?.trim()
  return normalized && DEPLOYMENT_CODE_PATTERN.test(normalized)
    ? normalized
    : undefined
}

/**
 * 每一次微信登录（包括用户点击重试）都从保存的部署码重新构造注册来源。
 * 返回新对象可以避免登录请求意外修改页面持有的来源状态。
 */
export function loginRegistrationSource(
  deploymentCode: string | undefined,
): { deploymentCode: string } | undefined {
  const normalized = normalizeDeploymentCode(deploymentCode)
  return normalized ? { deploymentCode: normalized } : undefined
}
