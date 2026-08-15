/**
 * 小程序后端地址。
 *
 * 不按开发者工具、真机或发布环境自动切换；需要更换服务器时只修改此常量。
 */
export const BASE_URL = 'https://115.159.67.35'

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
  targetCleaningDataApi: true,
  entryPreview: true,
}

/** 请求超时（ms） */
export const TIMEOUT = 15000

/** 本地存储 key */
export const STORAGE_KEYS = {
  session: 'ecobin_miniapp_session',
  factorySession: 'ecobin_factory_session_v1',
  factoryBindingKnown: 'ecobin_factory_binding_known_v1',
  factoryModeSuppressed: 'ecobin_factory_mode_suppressed_v1',
  pendingFactoryBindingToken: 'ecobin_factory_binding_token_v1',
  silentLoginSuppressed: 'ecobin_silent_login_suppressed',
  pendingDeviceEntry: 'ecobin_pending_device_entry',
  lastHandledDeviceEntry: 'ecobin_last_handled_device_entry',
  pendingOperationPrefix: 'ecobin_pending_operation_',
  withdrawalCreateIntent: 'ecobin_withdrawal_create_intent',
} as const
