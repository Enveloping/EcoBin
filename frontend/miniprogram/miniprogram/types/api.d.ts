/**
 * 后端接口数据类型定义（与 Java DTO/实体字段一一对应）。
 * 仅类型声明，编译期擦除，不产生运行时代码。
 */

/** 目标 /api/v1 成功信封；错误使用 ProblemDetail。 */
export interface Result<T> {
  code: 'OK'
  data: T
  requestId: string
}

export interface ProblemDetail {
  code: string
  message: string
  requestId: string
  retryable: boolean
  details: Record<string, unknown>
}

export type MiniappAudience = 'miniapp' | 'miniapp-staff'
export type EntryMode = 'USER' | 'CLEANING' | 'MANAGEMENT'

export interface OrganizationSummary {
  organizationCode: string
  displayName: string
}

/** 分页结果：org.enveloping.ecobin.common.result.PageResult（注意字段是 records 不是 list） */
export interface PageResult<T> {
  records: T[]
  total: number
  page: number
  pageSize: number
}

/** 目标接口的游标分页；nextCursor 为空表示已经到达末页。 */
export interface CursorPage<T> {
  items: T[]
  asOf: string
  nextCursor: string | null
}

/** 当前会话安全投影不再次返回 Bearer Token。 */
export interface MiniappSessionView {
  audience: MiniappAudience
  entryMode: EntryMode
  expiresAt: string
  organization: OrganizationSummary
  subjectUid: string
  organizationUserUid: string
  displayName: string
  capabilities: string[]
  phoneBound: boolean
}

/** 一次 wx.login 只返回一种 audience 的短期凭据。 */
export interface LoginResponse extends MiniappSessionView {
  accessToken: string
  tokenType: 'Bearer'
  isNewRegistration: boolean
}

export interface OrganizationAccountSummary {
  organizationUserUid: string
  organization: OrganizationSummary
  registeredAt: string
  selected: boolean
  phoneBound: boolean
}

export interface OrganizationAccountList {
  accounts: OrganizationAccountSummary[]
}

export type DeliveryOptionBlocker =
  | 'PHONE_BINDING_REQUIRED'
  | 'WALLET_DELIVERY_LIMIT_REACHED'
  | 'ASSET_UNAVAILABLE'
  | 'CONFIGURATION_NOT_APPLIED'
  | 'EDGE_OFFLINE'
  | 'DEVICE_BUSY'
  | 'PORT_DISABLED'
  | 'CURRENT_BAG_MISSING'
  | 'BASELINE_REMEASUREMENT_ACTIVE'
  | 'PORT_CLEAN_OPERATION_ACTIVE'
  | 'CLEAN_RESTARTED_CLEAN_REQUIRED'

export interface DeliveryPortOption {
  portNo: number
  displayName: string | null
  unitPriceYuanPerKg: string | null
  fullnessPercent: string | null
  deliveryAllowed: boolean
  blockers: DeliveryOptionBlocker[]
}

export interface DeliveryOptionsView {
  deviceCode: string
  displayName: string | null
  address: string | null
  deviceBusy: boolean
  asOf: string
  ports: DeliveryPortOption[]
}

export type DeliverySessionStatus = 'ACTIVE' | 'COMPLETED' | 'ENDED'

export type DeliverySessionPhase =
  | 'START_QUEUED'
  | 'IN_PROGRESS'
  | 'FINAL_RESULT_PENDING'
  | 'RECOVERY_REQUIRED'
  | 'BUSINESS_CONFIRMED'
  | 'PRE_START_FAILED'
  | 'DEVICE_RESTART_ABORTED'

export type DeliverySessionNextAction =
  | 'WAIT'
  | 'WAIT_ON_DEVICE'
  | 'VIEW_ORDER'
  | 'SESSION_ENDED'

export interface DeliverySessionAccepted {
  operationId: string
  resourceId: string
  sessionUid: string
  status: 'ACTIVE'
  phase: 'START_QUEUED'
  startAuthorizationExpiresAt: string
  statusUrl: string
  recommendedPollAfterMs: 1000
  nextActions: ['WAIT']
}

export interface DeliverySessionView {
  sessionUid: string
  status: DeliverySessionStatus
  phase: DeliverySessionPhase
  deviceCode: string
  portNo: number
  startedAt: string | null
  endedAt: string | null
  endReason: string | null
  deliveryOrderNo: string | null
  recommendedPollAfterMs: number | null
  nextActions: [DeliverySessionNextAction]
}

/** 当前小程序用户的钱包只读摘要。 */
export interface MiniappWalletView {
  walletVersion: number
  /** 待审核的可靠正返现，不属于正式钱包资金。 */
  pendingRewardYuan: string
  availableBalanceYuan: string
  withdrawalProcessingYuan: string
  asOf: string
}

export interface WithdrawalConfigurationView {
  versionNo: number
  hardLimitYuan: string
  manualMinimumYuan: string
  manualMaximumYuan: string
  manualReviewFreeThresholdYuan: string
  publishedAt: string
}

export type WithdrawalStatus =
  | 'PENDING_REVIEW'
  | 'READY_TO_SUBMIT'
  | 'CHANNEL_PROCESSING'
  | 'SUCCEEDED'
  | 'REJECTED'
  | 'LOCAL_CANCELLED'
  | 'LOCAL_ABORTED_BEFORE_CHANNEL'
  | 'CHANNEL_FAILED'
  | 'CHANNEL_CANCELLED'

export interface WithdrawalView {
  withdrawalNo: string
  status: WithdrawalStatus
  version: number
  amountYuan: string
  collectionMode: 'USER_CONFIRM' | 'AUTHORIZED'
  channelState: string | null
  channelErrorCode?: string | null
  channelStatusMessage?: string | null
  confirmationRequired: boolean
  cancellable: boolean
  channelBoundaryCrossed: boolean
  negativeBalancePaused: boolean
  postBoundaryRisk: boolean
  createdAt: string
  reviewedAt: string | null
  endedAt: string | null
}

export interface WithdrawalPage {
  items: WithdrawalView[]
  asOf: string
  nextCursor: string | null
}

export interface MerchantTransferConfirmationView {
  withdrawalNo: string
  appId: string
  mchId: string
  packageInfo: string
  channelState: 'WAIT_USER_CONFIRM'
}

export type MerchantTransferAuthorizationStatus =
  | 'NOT_OPENED'
  | 'PREPARING'
  | 'WAIT_USER_CONFIRM'
  | 'ACTIVE'
  | 'CLOSED'
  | 'EXPIRED'
  | 'UNKNOWN'

export interface MerchantTransferAuthorizationView {
  status: MerchantTransferAuthorizationStatus
  authorizationNo: string | null
  appId: string | null
  mchId: string | null
  packageInfo: string | null
  confirmationRequired: boolean
  confirmationExpiresAt: string | null
  authorizedAt: string | null
  closedAt: string | null
  closeReason: string | null
  lastSuccessfulQueryAt: string | null
}

export interface MerchantTransferAuthorizationAcceptedView {
  authorizationNo: string
  status: Exclude<MerchantTransferAuthorizationStatus, 'NOT_OPENED'>
  statusUrl: string
  recommendedPollAfterMs: number
}

export type WalletEntryType =
  | 'DELIVERY_INITIAL_REVIEW'
  | 'DELIVERY_CORRECTION'
  | 'WITHDRAWAL_FREEZE'
  | 'WITHDRAWAL_SUCCEEDED'
  | 'WITHDRAWAL_RELEASED'
  | 'MANUAL_ADJUSTMENT'

export type WalletEntrySourceType =
  | 'DELIVERY_ORDER'
  | 'WITHDRAWAL_ORDER'
  | 'MANUAL_ADJUSTMENT'

/** 当前用户的一条不可变钱包流水。 */
export interface PersonalWalletEntry {
  entryUid: string
  entrySequenceNo: number
  entryType: WalletEntryType
  availableDeltaYuan: string
  processingDeltaYuan: string
  availableBalanceAfterYuan: string
  withdrawalProcessingAfterYuan: string
  sourceType: WalletEntrySourceType
  sourceNo: string
  occurredAt: string
}

/** 个人信息视图：org.enveloping.ecobin.system.dto.UserProfileVO */
export interface UserProfileVO {
  id: number
  realName?: string
  phone?: string
  nickname?: string
  avatar?: string
  role: number
  status: number
}

export type DeliveryReviewStatus = 'PENDING' | 'APPROVED'

export type DeliveryRawWeightReliability =
  | 'RELIABLE'
  | 'INVALID'
  | 'MISSING'
  | 'INCONSISTENT'

export type DeliveryRawAmountReliability =
  | 'RELIABLE'
  | 'WEIGHT_UNRELIABLE'

export interface MiniappDeliveryOrderItem {
  deliveryOrderNo: string
  deviceCode: string
  portNo: number
  deviceOccurredAt: string | null
  receivedAt: string
  rawWeightKg: string | null
  rawAmountYuan: string | null
  rawWeightReliability: DeliveryRawWeightReliability
  rawAmountReliability: DeliveryRawAmountReliability
  reviewStatus: DeliveryReviewStatus
  currentRevisionNo: number
  finalWeightKg: string | null
  finalAmountYuan: string | null
  anomalyCodes: string[]
  photoCompleteness: string
}

export interface MiniappDeliverySource {
  eventUid: string
  sessionUid: string
  deviceCode: string
  portNo: number
  deviceOccurredAt: string | null
  receivedAt: string
}

export interface MiniappDeliveryRawFacts {
  firstPreOpenWeightGram: number | null
  finalPostCloseWeightGram: number | null
  netWeightGram: number | null
  weightKg: string | null
  unitPriceYuanPerKg: string | null
  amountYuan: string | null
  weightReliability: DeliveryRawWeightReliability
  amountReliability: DeliveryRawAmountReliability
  negativeWeightAnomaly: boolean
}

export interface MiniappDeliveryReviewProjection {
  status: DeliveryReviewStatus
  currentRevisionNo: number
  maxReviewAbsoluteWeightKg: string
  finalWeightKg: string | null
  finalAmountYuan: string | null
  firstApprovedAt: string | null
  /** 当前审核或纠正说明；会向订单所属用户展示。 */
  reason: string | null
}

export interface BagUseCycleItem {
  cycleUid: string
  status: 'ACTIVE' | 'CLOSED'
  startBasis: 'INITIAL_INSTALLED' | 'CLEAN_COMPLETE' | 'LEGACY_BACKFILL'
  deviceCode: string
  portNo: number
  installedAt: string
  removedAt: string | null
  deliveryOrderCount: number
}

export interface BagUseCyclePage {
  bagQr: string
  codeAuthKind: 'LEGACY' | 'HMAC_V1'
  unassignedLegacyDeliveryCount: number
  items: BagUseCycleItem[]
  asOf: string
  nextCursor: string | null
}

export interface BagTraceUser {
  organizationUserUid: string
  nickname: string
  maskedPhoneNumber: string | null
}

export interface BagTraceDeliveryOrderItem {
  deliveryOrderNo: string
  user: BagTraceUser
  deviceOccurredAt: string | null
  receivedAt: string
  rawWeightKg: string | null
  rawAmountYuan: string | null
  finalWeightKg: string | null
  finalAmountYuan: string | null
  reviewStatus: DeliveryReviewStatus
  reason: string | null
  photoCompleteness: 'COMPLETE' | 'INCOMPLETE'
}

export interface BagTraceDeliveryOrderPage {
  bagQr: string
  cycleUid: string
  items: BagTraceDeliveryOrderItem[]
  asOf: string
  nextCursor: string | null
}

export interface BagTraceDeliveryOrderDetail {
  bagQr: string
  cycleUid: string
  order: BagTraceDeliveryOrderItem
  photos: MiniappDeliveryPhoto[]
}

export interface MiniappDeliveryAnomaly {
  category: string
  code: string
  detectedAt: string
  message: string
}

export type DeliveryPhotoPosition =
  | 'BEFORE_INNER'
  | 'BEFORE_OUTER'
  | 'AFTER_INNER'
  | 'AFTER_OUTER'

export type DeliveryPhotoStatus =
  | 'UPLOAD_PENDING'
  | 'AVAILABLE'
  | 'PERMANENTLY_MISSING'

export interface MiniappDeliveryPhoto {
  position: DeliveryPhotoPosition
  status: DeliveryPhotoStatus
  url: string | null
  capturedAt: string | null
  missingReason: string | null
}

export interface MiniappDeliveryOrderDetail {
  deliveryOrderNo: string
  source: MiniappDeliverySource
  raw: MiniappDeliveryRawFacts
  review: MiniappDeliveryReviewProjection
  anomalies: MiniappDeliveryAnomaly[]
  photos: MiniappDeliveryPhoto[]
}

export type CleanFullnessStatus =
  | 'UNKNOWN'
  | 'CHECKING'
  | 'NOT_FULL'
  | 'SUSPECTED_FULL'
  | 'FULL'
  | 'SOURCE_FAILED'

export type CleanOptionBlocker =
  | 'CLEAN_CONFIGURATION_UNAVAILABLE'
  | 'CONFIGURATION_NOT_APPLIED'
  | 'EDGE_OFFLINE'
  | 'DEVICE_BUSY'
  | 'PORT_DISABLED'
  | 'CLEAN_OPERATION_ACTIVE'
  | 'PORT_WORK_ACTIVE'

export type CleanDeviceFilter =
  | 'ALL'
  | 'ONLINE'
  | 'NO_DELIVERY_24H'
  | 'NO_CLEAN_24H'
  | 'FULL'
  | 'FULL_TIMEOUT_2H'

export interface CleanDeviceItem {
  deviceCode: string
  displayName: string | null
  address: string | null
  connectionStatus: 'ONLINE' | 'OFFLINE' | 'UNKNOWN'
  portCount: number
  lastDeliveryAt: string | null
  lastCleanAt: string | null
  fullPortCount: number
  oldestFullSince: string | null
}

export interface RecoverableCleanOperation {
  operationUid: string
  portNo: number
  status: 'RECOVERY_REQUIRED'
  statusUrl: string
}

export interface CleanPortOption {
  portNo: number
  displayName: string | null
  currentBagQr: string | null
  fullnessStatus: CleanFullnessStatus
  fullnessPercent: string | null
  cleaningAllowed: boolean
  blockers: CleanOptionBlocker[]
}

export interface CleanOptionsView {
  deviceCode: string
  displayName: string | null
  address: string | null
  deviceBusy: boolean
  recoverableOperations: RecoverableCleanOperation[]
  asOf: string
  ports: CleanPortOption[]
}

export type CleanOperationStatus =
  | 'PREPARED'
  | 'EDGE_SAVED'
  | 'IN_PROGRESS'
  | 'RECOVERY_REQUIRED'
  | 'PRE_UNLOCK_ENDED'
  | 'COMPLETED'
  | 'ABORTED'

export interface CleanOperationAccepted {
  operationId: string
  resourceId: string
  operationUid: string
  status: 'PREPARED'
  version: number
  portNo: number
  installedBagQr: string
  startAuthorizationExpiresAt: string
  statusUrl: string
  recommendedPollAfterMs: number
  nextActions: ['WAIT']
}

export interface CleanOperationView {
  operationUid: string
  status: CleanOperationStatus
  version: number
  deviceCode: string
  portNo: number
  removedBagQr: string | null
  installedBagQr: string
  firstUnlockMayHaveExecuted: boolean
  cleanLockDeenergizedConfirmed: boolean
  cleanerPhysicalCloseConfirmed: boolean
  startAuthorizationExpiresAt: string
  executionDeadlineAt: string | null
  completedAt: string | null
  cleanRecordNo: string | null
  recommendedPollAfterMs: number | null
  nextActions: string[]
}

export type CleanResultKind = 'NORMAL' | 'SYSTEM_ANOMALY'
export type CleanPhotoCompleteness = 'COMPLETE' | 'INCOMPLETE'
export type CleanEffectiveWeightSource =
  | 'DEVICE_RECALCULATED'
  | 'MANUAL_SET'
  | 'MANUAL_CLEARED'

export interface CleanRecordItem {
  cleanRecordNo: string
  operationUid: string
  cleanerUserUid: string
  deviceCode: string
  portNo: number
  removedBagQr: string | null
  installedBagQr: string
  deviceCompletedAt: string | null
  originalRecalculatedRemovedNetWeightKg: string | null
  effectiveRemovedNetWeightKg: string | null
  effectiveWeightSource: CleanEffectiveWeightSource
  weightReliability: 'RELIABLE' | 'UNAVAILABLE' | 'INVALID'
  resultKind: CleanResultKind
  anomalyCodes: string[]
  photoCompleteness: CleanPhotoCompleteness
  recordRemark: string | null
  version: number
}

export interface CleanRecordSource {
  operationUid: string
  eventUid: string
  commandUid: string
  deviceCode: string
  portNo: number
  cleanerUserUid: string
  cleanConfigVersionNo: number
  deviceCompletedAt: string | null
  backendReceivedAt: string
  completedAt: string
}

export interface CleanBagFacts {
  removedBagBindingState: 'BOUND' | 'MISSING'
  removedBagQr: string | null
  installedBagQr: string
}

export interface CleanWeightFacts {
  preUnlockStatus?: 'RELIABLE' | 'FAILED' | null
  preUnlockWeightKg?: string | null
  oldBaselineState?: 'TRUSTED' | 'UNTRUSTED' | 'MISSING' | null
  oldBaselineWeightKg?: string | null
  deviceRemovedNetWeightStatus?: 'RELIABLE' | 'FAILED' | null
  deviceRemovedNetWeightKg?: string | null
  recalculatedRemovedNetWeightStatus?:
    | 'RELIABLE'
    | 'UNAVAILABLE'
    | 'INVALID'
    | null
  recalculatedRemovedNetWeightKg?: string | null
  finalTotalWeightStatus?: 'RELIABLE' | 'INVALID' | 'FAILED' | null
  finalTotalWeightKg?: string | null
  candidateNewBaselineWeightKg?: string | null
}

export interface CleanBaselineSummary {
  established: boolean
  versionNo: number | null
  installedBagQr: string | null
  baselineWeightKg: string | null
  establishedAt: string | null
}

export interface CleanDetectionSummary {
  detectionUid: string | null
  status: string | null
  finalResult: string | null
  failureCode: string | null
  completedAt: string | null
}

export interface MiniappCleanAnomaly {
  code: string
  detectedAt: string
}

export interface CleanPhoto {
  position: DeliveryPhotoPosition
  status: DeliveryPhotoStatus
  url: string | null
  capturedAt: string | null
  missingReason: string | null
}

export interface CleanEffectiveValue {
  removedNetWeightKg: string | null
  source: CleanEffectiveWeightSource
  includedInKnownWeightStatistics: boolean
  recordRemark: string | null
  version: number
}

export interface MiniappCleanRecordDetail {
  cleanRecordNo: string
  source: CleanRecordSource
  bags: CleanBagFacts
  weights: CleanWeightFacts
  newBaseline: CleanBaselineSummary
  postCleanDetection: CleanDetectionSummary | null
  resultKind: CleanResultKind
  anomalies: MiniappCleanAnomaly[]
  photos: CleanPhoto[]
  effective: CleanEffectiveValue
}

/** 设备：org.enveloping.ecobin.device.entity.Device */
export interface Device {
  id: number
  tenantId: number
  sn: string
  name: string
  type?: number
  lat?: number
  lng?: number
  address?: string
  /** 状态：0-离线 1-在线 2-维护中 */
  status?: number
}

/** 投口：org.enveloping.ecobin.device.entity.Door */
export interface Door {
  id: number
  tenantId: number
  deviceId: number
  doorIndex: number
  name?: string
  wasteType1: number
  wasteType2?: number
  price?: string
  enabled?: number
  sortOrder?: number
}
