import { parseOrdinaryDeviceLink } from './ordinary-device-link'

const DEPLOYMENT_CODE_PATTERN = /^Dp_[A-Za-z0-9_-]{6,61}$/

function decodeOption(value: string): string | undefined {
  try {
    return decodeURIComponent(value)
  } catch {
    return undefined
  }
}

function normalizeDeploymentCode(
  value: string | undefined,
): string | undefined {
  const normalized = value?.trim()
  return normalized && DEPLOYMENT_CODE_PATTERN.test(normalized)
    ? normalized
    : undefined
}

/**
 * 登录页只把契约规定的公开部署码送往后端。
 *
 * 支持直接页面参数、微信普通链接二维码的 q、纯部署码 scene，以及
 * query/URL 形式的 scene。普通链接的域名、路径和 AppID 前缀由微信后台
 * 负责路由；客户端只提取 deploymentCode，不得阻断微信登录。
 */
export function registrationDeploymentCode(
  options: Record<string, string | undefined>,
): string | undefined {
  const direct = options.deploymentCode
    ? decodeOption(options.deploymentCode)
    : undefined
  const directCode = normalizeDeploymentCode(direct)
  if (directCode) return directCode

  const ordinaryLink = parseOrdinaryDeviceLink(options.q)
  if (ordinaryLink?.deploymentCode) return ordinaryLink.deploymentCode

  if (!options.scene) return undefined
  const scene = decodeOption(options.scene)?.trim()
  if (!scene) return undefined

  const sceneCode = normalizeDeploymentCode(scene)
  if (sceneCode) return sceneCode

  const matched = /(?:^|[?&])deploymentCode=([^&]+)/.exec(scene)
  if (!matched) return undefined
  return normalizeDeploymentCode(decodeOption(matched[1]))
}
