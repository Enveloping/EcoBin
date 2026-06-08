// ===== 通用响应 =====

/** 后端统一响应包裹 Result<T> */
export interface Result<T = unknown> {
  code: number;
  message: string;
  data: T;
}

/** 分页响应 PageResult<T> */
export interface PageResult<T> {
  records: T[];
  total: number;
  page: number;
  pageSize: number;
}

/** 分页请求参数（页码从 1 开始，单页上限 200） */
export interface PageParams {
  page?: number;
  pageSize?: number;
  [key: string]: unknown;
}

// ===== 认证 =====

export type UserType = 'admin' | 'tenant';

export interface LoginRequest {
  userType: UserType;
  username: string;
  password: string;
}

export interface LoginResponse {
  token: string;
  userId: number;
  tenantId: number;
  username: string;
  realName: string;
  role: number;
  nickname?: string;
  avatar?: string;
}

// ===== 系统域实体 =====

/** 平台管理员 sys_admin（role 9/8） */
export interface Admin {
  id?: number;
  username: string;
  /** 只写：新建/改密时传，响应不返回 */
  password?: string;
  realName?: string;
  role: number;
  status: number;
  createTime?: string;
  updateTime?: string;
}

/** 租户 sys_tenant（role 7；id 即 tenant_id，恒 >1） */
export interface Tenant {
  id?: number;
  name: string;
  code?: string;
  username?: string;
  /** 只写 */
  password?: string;
  miniappAppid?: string | null;
  /** 只写 */
  miniappSecret?: string;
  merchantNo?: string;
  contactName?: string;
  contactPhone?: string;
  address?: string;
  status: number;
  createTime?: string;
  updateTime?: string;
}

/** 终端用户 sys_user（role 3/2/1，脱敏） */
export interface User {
  id: number;
  realName?: string;
  phone?: string;
  email?: string;
  nickname?: string;
  avatar?: string;
  role: number;
  status: number;
  balance?: number;
  pendingBalance?: number;
  createTime?: string;
}

// ===== 设备域实体 =====

export interface Device {
  id?: number;
  sn: string;
  name: string;
  type?: number;
  lat?: number;
  lng?: number;
  address?: string;
  status?: number;
  tenantId?: number;
  createTime?: string;
  updateTime?: string;
}

export interface Door {
  id?: number;
  deviceId: number;
  doorIndex: number;
  name?: string;
  wasteType1: number;
  wasteType2?: number;
  price?: number;
  enabled?: number;
  sortOrder?: number;
  createTime?: string;
  updateTime?: string;
}

// ===== 业务域实体 =====

export interface DeliveryOrder {
  id: number;
  orderSn: string;
  deliveryToken?: string;
  deviceId: number;
  doorId: number;
  userId: number;
  wasteType1: number;
  wasteType2?: number;
  weight?: number;
  price?: number;
  score?: number;
  loginType?: number;
  status: number;
  deliveryStatus: number;
  createTime: string;
}

export interface CleanOrder {
  id: number;
  orderSn: string;
  deviceId: number;
  doorId?: number;
  userId: number;
  wasteType1: number;
  wasteType2?: number;
  weight?: number;
  auditStatus: number;
  status: number;
  createTime?: string;
  updateTime?: string;
}

export interface WithdrawOrder {
  id: number;
  userId: number;
  amount: number;
  status: number;
  auditBy?: number;
  auditTime?: string;
  auditRemark?: string;
  transferNo?: string;
  createTime: string;
}
