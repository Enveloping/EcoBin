const DEPLOYMENT_CODE_PATTERN = /^Dp_[A-Za-z0-9_-]{6,61}$/
const MAX_LINK_LENGTH = 2048

export interface DeviceLink {
  deploymentCode: string
}

function decodeComponent(value: string): string | undefined {
  try {
    return decodeURIComponent(value)
  } catch {
    return undefined
  }
}

function deploymentCodeFromQuery(query: string): string | undefined {
  let deploymentCode: string | undefined
  for (const pair of query.split('&')) {
    if (!pair) continue
    const separator = pair.indexOf('=')
    if (separator <= 0) continue
    const key = decodeComponent(pair.slice(0, separator))
    if (key !== 'deploymentCode') continue
    if (deploymentCode !== undefined) return undefined
    deploymentCode = decodeComponent(pair.slice(separator + 1))
  }
  const normalized = deploymentCode?.trim()
  return normalized && DEPLOYMENT_CODE_PATTERN.test(normalized)
    ? normalized
    : undefined
}

/**
 * 解析小程序内部 wx.scanCode 返回的二维码原始链接。
 *
 * 域名、路径和路径中的 AppID 由微信后台二维码规则负责路由，客户端不校验。
 * 客户端只提取 deploymentCode；登录请求的 appId 始终取当前运行小程序，
 * 后端再按 appId 恢复机构并校验 deploymentCode 的真实归属。
 */
export function parseDeviceLinkUrl(
  rawLink: string | undefined,
): DeviceLink | undefined {
  const value = rawLink?.trim()
  if (!value || value.length > MAX_LINK_LENGTH) return undefined

  const queryStart = value.indexOf('?')
  if (queryStart < 0) return undefined
  const fragmentStart = value.indexOf('#', queryStart + 1)
  const query = value.slice(
    queryStart + 1,
    fragmentStart < 0 ? value.length : fragmentStart,
  )
  const deploymentCode = deploymentCodeFromQuery(query)
  return deploymentCode ? { deploymentCode } : undefined
}

/**
 * 解析微信“扫普通链接二维码打开小程序”放在页面 options.q 中的链接。
 * q 外层严格只解码一次，内部查询参数由 parseDeviceLinkUrl 各解码一次。
 */
export function parseOrdinaryDeviceLink(
  encodedLink: string | undefined,
): DeviceLink | undefined {
  const raw = encodedLink?.trim()
  if (!raw || raw.length > MAX_LINK_LENGTH * 3) return undefined
  const decoded = decodeComponent(raw)
  return decoded ? parseDeviceLinkUrl(decoded) : undefined
}
