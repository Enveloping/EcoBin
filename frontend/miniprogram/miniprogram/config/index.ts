/**
 * 全局配置。
 *
 * baseURL 开发期指向本地后端；真机/预览需在小程序后台配置 request 合法域名，
 * 或在开发者工具「详情 → 本地设置」勾选「不校验合法域名」。
 */

function resolveBaseUrl(): string {
  try {
    if (wx.getSystemInfoSync().platform === 'devtools') {
      return 'http://localhost:8080'
    }
  } catch {
    // 非微信运行时只用于静态检查，不发起真实请求。
  }
  return 'https://www.jinshoubao.com'
}

/** 开发者工具访问本机；真机、体验版和正式版访问生产 HTTPS 域名。 */
export const BASE_URL = resolveBaseUrl()

export const FEATURES: Readonly<{
  targetDeliveryOrderApi: boolean
  targetWalletApi: boolean
  targetWithdrawalApi: boolean
  targetCleaningDataApi: boolean
  entryPreview: boolean
}> = {
  targetDeliveryOrderApi: true,
  targetWalletApi: true,
  targetWithdrawalApi: true,
  targetCleaningDataApi: false,
  entryPreview: true,
}

/** 开发演示开关：true 时允许扫码或手动填写规范设备二维码链接。 */
export const test = true

/** 请求超时（ms） */
export const TIMEOUT = 15000

/** 本地存储 key */
export const STORAGE_KEYS = {
  session: 'ecobin_miniapp_session',
  phoneAutoPrompted: 'ecobin_phone_auto_prompted',
  pendingDeviceEntry: 'ecobin_pending_device_entry',
  lastHandledDeviceEntry: 'ecobin_last_handled_device_entry',
  pendingOperationPrefix: 'ecobin_pending_operation_',
  withdrawalCreateIntent: 'ecobin_withdrawal_create_intent',
} as const
