import { parseOrdinaryDeviceLink } from './ordinary-device-link'
import { normalizeDeviceCode } from './login-registration-source'

function decodeOption(value: string): string | undefined {
  try {
    return decodeURIComponent(value)
  } catch {
    return undefined
  }
}

/**
 * 登录页只把契约规定的设备公开码送往后端。
 *
 * 支持直接页面参数、微信普通链接二维码的 q、纯设备公开码 scene，以及
 * query/URL 形式的 scene。普通链接的域名、路径和 AppID 前缀由微信后台
 * 负责路由；客户端只提取 deviceCode，不得阻断微信登录。
 */
export function registrationDeviceCode(
  options: Record<string, string | undefined>,
): string | undefined {
  const direct = options.deviceCode
    ? decodeOption(options.deviceCode)
    : undefined
  const directCode = normalizeDeviceCode(direct)
  if (directCode) return directCode

  const ordinaryLink = parseOrdinaryDeviceLink(options.q)
  if (ordinaryLink?.deviceCode) return ordinaryLink.deviceCode

  if (!options.scene) return undefined
  const scene = decodeOption(options.scene)?.trim()
  if (!scene) return undefined

  const sceneCode = normalizeDeviceCode(scene)
  if (sceneCode) return sceneCode

  const matched = /(?:^|[?&])deviceCode=([^&]+)/.exec(scene)
  if (!matched) return undefined
  return normalizeDeviceCode(decodeOption(matched[1]))
}
