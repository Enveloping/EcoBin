/**
 * 全局配置。
 *
 * baseURL 开发期指向本地后端；真机/预览需在小程序后台配置 request 合法域名，
 * 或在开发者工具「详情 → 本地设置」勾选「不校验合法域名」。
 */

/** 后端接口基地址（按需改成你的后端地址） */
//export const BASE_URL = 'http://115.159.67.35:8080'
export const BASE_URL = 'http://localhost:8080'

/** 开发演示开关：true 时扫码开门前允许选择扫码或手动填写投口 ID。 */
export const test = true

/** 请求超时（ms） */
export const TIMEOUT = 15000

/** 本地存储 key */
export const STORAGE_KEYS = {
  session: 'ecobin_miniapp_session',
  pendingOperationPrefix: 'ecobin_pending_operation_',
} as const
