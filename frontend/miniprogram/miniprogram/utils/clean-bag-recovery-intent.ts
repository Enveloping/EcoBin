export type CleanBagRecoveryDecision =
  | 'RETAIN_OLD_BAG'
  | 'USE_RESERVED_NEW_BAG'

export type CleanBagRecoveryState =
  | 'COMPLETED'
  | 'BASELINE_PENDING'
  | 'BASELINE_REQUIRED'

export interface RecoverInterruptedCleanBagBody {
  actualBagQr: string
  actualBagConfirmed: true
  emptyBagConfirmed: boolean
  expectedOperationVersion: number
  reason: string
}

export interface PendingCleanBagRecoveryIntent {
  schemaVersion: 1
  deviceCode: string
  portNo: number
  operationUid: string
  originalBagQr: string | null
  reservedNewBagQr: string
  decision: CleanBagRecoveryDecision
  body: RecoverInterruptedCleanBagBody
  idempotencyKey: string
  lastState: CleanBagRecoveryState | null
  createdAt: string
}

export interface InterruptedCleanBagRecoveryAccepted {
  recoveryUid: string
  operationUid: string
  state: CleanBagRecoveryState
  decision: CleanBagRecoveryDecision
  actualBagQr: string
  baselineMeasurementUid: string | null
  baselineTaskUid: string | null
  nextAction:
    | 'WAIT_FOR_NEXT_BUSINESS'
    | 'WAIT_FOR_EMPTY_BAG_BASELINE'
    | 'RETRY_EMPTY_BAG_BASELINE'
}

export type CleanBagRecoveryNextStep =
  | 'SCAN'
  | 'WAIT'
  | 'RETRY'
  | 'COMPLETED'
  | 'UNAVAILABLE'

export type CleanBagRecoveryFailureDisposition =
  | 'RETAIN'
  | 'REFRESH'
  | 'RESCAN'
  | 'UNAVAILABLE'

export interface NewCleanBagRecoveryIntent {
  deviceCode: string
  portNo: number
  operationUid: string
  expectedOperationVersion: number
  originalBagQr: string | null
  reservedNewBagQr: string
  actualBagQr: string
  emptyBagConfirmed: boolean
  reason: string
  idempotencyKey: string
}

const DEVICE_CODE = /^Dv_[A-Za-z0-9_-]{24,61}$/
const RAW_BAG_QR = /^EB1_K[0-9A-Z]{1,6}_[0-9A-HJKMNP-TV-Z]{26}_[0-9A-HJKMNP-TV-Z]{20}$/
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const STORAGE_KEY = 'ecobin_pending_clean_bag_recovery'
const STATES = new Set<CleanBagRecoveryState>([
  'COMPLETED',
  'BASELINE_PENDING',
  'BASELINE_REQUIRED',
])

export function classifyRecoveryBag(
  originalBagQr: string | null,
  reservedNewBagQr: string,
  scannedBagQr: string,
): CleanBagRecoveryDecision | null {
  if (originalBagQr && scannedBagQr === originalBagQr) {
    return 'RETAIN_OLD_BAG'
  }
  if (scannedBagQr === reservedNewBagQr) {
    return 'USE_RESERVED_NEW_BAG'
  }
  return null
}

function validIntent(value: unknown): value is PendingCleanBagRecoveryIntent {
  if (!value || typeof value !== 'object') return false
  const intent = value as Partial<PendingCleanBagRecoveryIntent>
  const body = intent.body as Partial<RecoverInterruptedCleanBagBody> | undefined
  const decision = classifyRecoveryBag(
    intent.originalBagQr ?? null,
    intent.reservedNewBagQr ?? '',
    body?.actualBagQr ?? '',
  )
  return intent.schemaVersion === 1
    && DEVICE_CODE.test(intent.deviceCode ?? '')
    && Number.isInteger(intent.portNo)
    && (intent.portNo ?? 0) >= 1
    && (intent.portNo ?? 0) <= 6
    && UUID_V4.test(intent.operationUid ?? '')
    && UUID_V4.test(intent.idempotencyKey ?? '')
    && (intent.originalBagQr === null
      || RAW_BAG_QR.test(intent.originalBagQr ?? ''))
    && RAW_BAG_QR.test(intent.reservedNewBagQr ?? '')
    && decision !== null
    && intent.decision === decision
    && body?.actualBagConfirmed === true
    && Number.isSafeInteger(body.expectedOperationVersion)
    && (body.expectedOperationVersion ?? -1) >= 0
    && typeof body.reason === 'string'
    && body.reason.trim().length >= 1
    && body.reason.length <= 500
    && (
      (decision === 'RETAIN_OLD_BAG' && body.emptyBagConfirmed === false)
      || (
        decision === 'USE_RESERVED_NEW_BAG'
        && body.emptyBagConfirmed === true
      )
    )
    && (intent.lastState === null
      || (intent.lastState !== undefined && STATES.has(intent.lastState)))
    && typeof intent.createdAt === 'string'
}

export function newPendingCleanBagRecoveryIntent(
  input: NewCleanBagRecoveryIntent,
): PendingCleanBagRecoveryIntent {
  const decision = classifyRecoveryBag(
    input.originalBagQr,
    input.reservedNewBagQr,
    input.actualBagQr,
  )
  if (!decision) throw new TypeError('实际袋不是原袋或本次预留新袋')
  if (
    (decision === 'USE_RESERVED_NEW_BAG' && !input.emptyBagConfirmed)
    || (decision === 'RETAIN_OLD_BAG' && input.emptyBagConfirmed)
  ) {
    throw new TypeError('空袋确认与实际袋不一致')
  }
  const intent: PendingCleanBagRecoveryIntent = {
    schemaVersion: 1,
    deviceCode: input.deviceCode,
    portNo: input.portNo,
    operationUid: input.operationUid,
    originalBagQr: input.originalBagQr,
    reservedNewBagQr: input.reservedNewBagQr,
    decision,
    body: {
      actualBagQr: input.actualBagQr,
      actualBagConfirmed: true,
      emptyBagConfirmed: input.emptyBagConfirmed,
      expectedOperationVersion: input.expectedOperationVersion,
      reason: input.reason.trim(),
    },
    idempotencyKey: input.idempotencyKey,
    lastState: null,
    createdAt: new Date().toISOString(),
  }
  if (!validIntent(intent)) throw new TypeError('清运中断袋恢复意图字段无效')
  return intent
}

export function rememberCleanBagRecoveryIntent(
  intent: PendingCleanBagRecoveryIntent,
): void {
  if (!validIntent(intent)) throw new TypeError('不能保存无效的袋恢复意图')
  wx.setStorageSync(STORAGE_KEY, intent)
}

export function restoreCleanBagRecoveryIntent():
PendingCleanBagRecoveryIntent | null {
  let stored: unknown
  try {
    stored = wx.getStorageSync(STORAGE_KEY) as unknown
  } catch {
    return null
  }
  if (validIntent(stored)) return stored
  try {
    wx.removeStorageSync(STORAGE_KEY)
  } catch {
    // 无效的恢复意图不能继续提交。
  }
  return null
}

export function forgetCleanBagRecoveryIntent(): void {
  wx.removeStorageSync(STORAGE_KEY)
}

export function acceptCleanBagRecoveryIntent(
  intent: PendingCleanBagRecoveryIntent,
  accepted: InterruptedCleanBagRecoveryAccepted,
): PendingCleanBagRecoveryIntent {
  if (
    accepted.operationUid !== intent.operationUid
    || accepted.actualBagQr !== intent.body.actualBagQr
    || accepted.decision !== intent.decision
    || !UUID_V4.test(accepted.recoveryUid)
    || !STATES.has(accepted.state)
  ) {
    throw new Error('袋恢复受理结果与本地意图不一致')
  }
  const expectedAction = accepted.state === 'COMPLETED'
    ? 'WAIT_FOR_NEXT_BUSINESS'
    : accepted.state === 'BASELINE_PENDING'
      ? 'WAIT_FOR_EMPTY_BAG_BASELINE'
      : 'RETRY_EMPTY_BAG_BASELINE'
  if (accepted.nextAction !== expectedAction) {
    throw new Error('袋恢复受理结果的下一步无效')
  }
  const next: PendingCleanBagRecoveryIntent = {
    ...intent,
    lastState: accepted.state,
  }
  rememberCleanBagRecoveryIntent(next)
  return next
}

export function cleanBagRecoveryNextStep(
  operationStatus: string,
  nextActions: string[],
  recoveryWasSubmitted: boolean,
): CleanBagRecoveryNextStep {
  if (operationStatus !== 'ABORTED') return 'UNAVAILABLE'
  if (nextActions.includes('SCAN_ACTUAL_BAG')) return 'SCAN'
  if (nextActions.includes('WAIT_FOR_EMPTY_BAG_BASELINE')) return 'WAIT'
  if (nextActions.includes('RETRY_EMPTY_BAG_BASELINE')) return 'RETRY'
  return recoveryWasSubmitted && nextActions.length === 0
    ? 'COMPLETED'
    : 'UNAVAILABLE'
}

export function canDiscardCleanOperationIntentForRecovery(
  cleanIntentOperationUid: string | null,
  recoveryOperationUid: string,
  recoveryOperationStatus: string,
): boolean {
  return cleanIntentOperationUid === recoveryOperationUid
    && recoveryOperationStatus === 'ABORTED'
}

export function cleanBagRecoveryFailureDisposition(
  status: number,
  _code: string,
): CleanBagRecoveryFailureDisposition {
  if (status === 409) return 'REFRESH'
  if (status === 400 || status === 422) return 'RESCAN'
  if (status >= 400 && status < 500) return 'UNAVAILABLE'
  return 'RETAIN'
}
