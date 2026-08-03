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

export type DeliveryOptionBlocker =
  | 'PHONE_BINDING_REQUIRED'
  | 'WALLET_DELIVERY_LIMIT_REACHED'
  | 'DEPLOYMENT_NOT_ENABLED'
  | 'BUSINESS_SWITCH_DISABLED'
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
  deploymentCode: string
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
  deploymentCode: string
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
  deploymentCode: string
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
  deploymentCode: string
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

/** 清运订单：org.enveloping.ecobin.business.entity.CleanOrder */
export interface CleanOrder {
  id: number
  tenantId: number
  createTime: string
  updateTime?: string
  orderSn?: string
  deviceId: number
  doorId?: number
  /** 本次清运清走的垃圾袋编号 */
  bagQr?: string
  userId: number
  wasteType1?: number
  wasteType2?: number
  /** 实际清运量（kg），等同 netWeight，兼容旧字段 */
  weight?: string
  /** 清运毛重（kg，设备上报的满袋重量） */
  grossWeight?: string
  /** 去皮重量（kg，该投口当前垃圾袋去皮） */
  tareWeight?: string
  /** 实际清运量（kg）= 毛重 - 去皮 */
  netWeight?: string
  /** @deprecated 审核流程已废弃 */
  auditStatus?: number
  status?: number
}

/** 开清运门请求：org.enveloping.ecobin.business.dto.CleanOpenRequest */
export interface CleanOpenRequest {
  doorId: number
  bagNo: string
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
