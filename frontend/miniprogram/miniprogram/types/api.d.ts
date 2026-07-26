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

/** 当前会话安全投影不再次返回 Bearer Token。 */
export interface MiniappSessionView {
  audience: MiniappAudience
  entryMode: EntryMode
  expiresAt: string
  organization: OrganizationSummary
  subjectUid: string
  displayName: string
  capabilities: string[]
}

/** 一次 wx.login 只返回一种 audience 的短期凭据。 */
export interface LoginResponse extends MiniappSessionView {
  accessToken: string
  tokenType: 'Bearer'
  isNewRegistration: boolean
}

/** 钱包视图：org.enveloping.ecobin.business.dto.WalletVO */
export interface WalletVO {
  balance: string
  pendingBalance: string
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

/** 投递订单：org.enveloping.ecobin.business.entity.DeliveryOrder */
export interface DeliveryOrder {
  id: number
  tenantId: number
  createTime: string
  orderSn?: string
  /** 幂等键：上传后建单时落 OneNet 消息 id / MQ messageId */
  deliveryToken?: string
  deviceId?: number
  doorId?: number
  userId: number
  wasteType1?: number
  wasteType2?: number
  weight?: string
  price?: string
  rawAmountYuan?: string | null
  finalAmountYuan?: string | null
  score?: number
  loginType?: number
  status?: number
  /** 投递阶段：0-进行中 1-已完成 */
  deliveryStatus?: number
  /** 审核状态：0-待审核 1-审核通过 2-审核拒绝（通过后才返现入账） */
  auditStatus?: number
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

/** 提现订单：org.enveloping.ecobin.business.entity.WithdrawOrder */
export interface WithdrawOrder {
  id: number
  tenantId: number
  createTime: string
  userId: number
  amount: string
  /** 状态：0-待审核 1-已通过 2-已驳回 */
  status?: number
  auditTime?: string
  auditRemark?: string
  transferNo?: string
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
