import type {
  CleanOperationAccepted,
  CleanOperationStatus,
  CleanOperationView,
} from '../types/api'

const DEPLOYMENT_CODE = /^Dp_[A-Za-z0-9_-]{6,61}$/
const RAW_BAG_QR = /^[A-Za-z0-9_-]{8,64}$/
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const STATUS_URL = /^\/api\/v1\/miniapp\/clean-operations\/([0-9a-f-]{36})$/i
const PENDING_CLEAN_OPERATION_KEY = 'ecobin_pending_clean_operation'
const STATUSES = new Set<CleanOperationStatus>([
  'PREPARED',
  'EDGE_SAVED',
  'IN_PROGRESS',
  'RECOVERY_REQUIRED',
  'PRE_OPEN_ENDED',
  'COMPLETED',
])

export interface PendingCleanOperationIntent {
  schemaVersion: 1
  deploymentCode: string
  portNo: number
  installedBagQr: string
  idempotencyKey: string
  operationUid: string | null
  statusUrl: string | null
  recommendedPollAfterMs: number
  lastStatus: CleanOperationStatus | null
  cleanRecordNo: string | null
  createdAt: string
}

export function parseCleaningDeploymentCode(raw: string): string {
  const value = raw.trim()
  if (DEPLOYMENT_CODE.test(value)) return value
  const match = value.match(/[?&]deploymentCode=([^&#]+)/)
  if (!match) return ''
  try {
    const decoded = decodeURIComponent(match[1]).trim()
    return DEPLOYMENT_CODE.test(decoded) ? decoded : ''
  } catch {
    return ''
  }
}

/** 袋码只接受扫码器返回的原始文本；URL、空格内嵌值和手工输入都无效。 */
export function parseRawBagQr(raw: string): string {
  const value = raw.trim()
  return RAW_BAG_QR.test(value) ? value : ''
}

function validPollDelay(value: unknown): value is number {
  return typeof value === 'number'
    && Number.isFinite(value)
    && value >= 250
    && value <= 60000
}

function isPendingCleanOperationIntent(
  value: unknown,
): value is PendingCleanOperationIntent {
  if (!value || typeof value !== 'object') return false
  const intent = value as Partial<PendingCleanOperationIntent>
  if (
    intent.schemaVersion !== 1
    || !DEPLOYMENT_CODE.test(intent.deploymentCode ?? '')
    || !Number.isSafeInteger(intent.portNo)
    || (intent.portNo ?? 0) < 1
    || !RAW_BAG_QR.test(intent.installedBagQr ?? '')
    || !UUID_V4.test(intent.idempotencyKey ?? '')
    || !validPollDelay(intent.recommendedPollAfterMs)
    || typeof intent.createdAt !== 'string'
  ) {
    return false
  }
  if (intent.operationUid === null && intent.statusUrl === null) {
    return intent.lastStatus === null && intent.cleanRecordNo === null
  }
  if (
    typeof intent.operationUid !== 'string'
    || !UUID_V4.test(intent.operationUid)
    || typeof intent.statusUrl !== 'string'
  ) {
    return false
  }
  const statusMatch = STATUS_URL.exec(intent.statusUrl)
  if (
    !statusMatch
    || statusMatch[1].toLowerCase() !== intent.operationUid.toLowerCase()
  ) {
    return false
  }
  return (
    intent.lastStatus === null
    || (
      intent.lastStatus !== undefined
      && STATUSES.has(intent.lastStatus)
    )
  ) && (
    intent.cleanRecordNo === null
    || (
      intent.cleanRecordNo !== undefined
      && RAW_BAG_QR.test(intent.cleanRecordNo)
    )
  )
}

export function newPendingCleanOperationIntent(
  deploymentCode: string,
  portNo: number,
  installedBagQr: string,
  idempotencyKey: string,
): PendingCleanOperationIntent {
  const intent: PendingCleanOperationIntent = {
    schemaVersion: 1,
    deploymentCode,
    portNo,
    installedBagQr,
    idempotencyKey,
    operationUid: null,
    statusUrl: null,
    recommendedPollAfterMs: 1000,
    lastStatus: null,
    cleanRecordNo: null,
    createdAt: new Date().toISOString(),
  }
  if (!isPendingCleanOperationIntent(intent)) {
    throw new TypeError('清运操作意图字段无效')
  }
  return intent
}

export function rememberCleanOperationIntent(
  intent: PendingCleanOperationIntent,
): void {
  if (!isPendingCleanOperationIntent(intent)) {
    throw new TypeError('不能保存无效的清运操作意图')
  }
  wx.setStorageSync(PENDING_CLEAN_OPERATION_KEY, intent)
}

export function restoreCleanOperationIntent(): PendingCleanOperationIntent | null {
  let stored: unknown
  try {
    stored = wx.getStorageSync(PENDING_CLEAN_OPERATION_KEY) as unknown
  } catch {
    return null
  }
  if (isPendingCleanOperationIntent(stored)) return stored
  try {
    wx.removeStorageSync(PENDING_CLEAN_OPERATION_KEY)
  } catch {
    // 无效值不会在当前进程中继续使用。
  }
  return null
}

export function forgetCleanOperationIntent(): void {
  wx.removeStorageSync(PENDING_CLEAN_OPERATION_KEY)
}

export function sameCleanOperationRequest(
  intent: PendingCleanOperationIntent,
  deploymentCode: string,
  portNo: number,
  installedBagQr: string,
): boolean {
  return intent.deploymentCode === deploymentCode
    && intent.portNo === portNo
    && intent.installedBagQr === installedBagQr
}

export function acceptCleanOperationIntent(
  intent: PendingCleanOperationIntent,
  accepted: CleanOperationAccepted,
): PendingCleanOperationIntent {
  if (
    accepted.portNo !== intent.portNo
    || accepted.installedBagQr !== intent.installedBagQr
    || accepted.operationUid !== accepted.resourceId
    || accepted.operationUid !== accepted.operationId
  ) {
    throw new Error('清运受理结果与本地意图不一致')
  }
  const next: PendingCleanOperationIntent = {
    ...intent,
    operationUid: accepted.operationUid,
    statusUrl: accepted.statusUrl,
    recommendedPollAfterMs: accepted.recommendedPollAfterMs,
    lastStatus: accepted.status,
  }
  rememberCleanOperationIntent(next)
  return next
}

export function projectCleanOperationIntent(
  intent: PendingCleanOperationIntent,
  projection: CleanOperationView,
): PendingCleanOperationIntent {
  if (
    projection.operationUid !== intent.operationUid
    || projection.deploymentCode !== intent.deploymentCode
    || projection.portNo !== intent.portNo
    || projection.installedBagQr !== intent.installedBagQr
  ) {
    throw new Error('清运状态与本地意图不一致')
  }
  const next: PendingCleanOperationIntent = {
    ...intent,
    lastStatus: projection.status,
    cleanRecordNo: projection.cleanRecordNo,
    recommendedPollAfterMs:
      projection.recommendedPollAfterMs ?? intent.recommendedPollAfterMs,
  }
  rememberCleanOperationIntent(next)
  return next
}
