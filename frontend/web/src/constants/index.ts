// 枚举映射，对照 docs/api-frontend.md §10。每项含 label + antd Tag color。

export interface EnumItem {
  label: string;
  color?: string;
}

export type EnumMap = Record<number, EnumItem>;

/** 角色 role */
export const ROLE: EnumMap = {
  9: { label: '超级管理员', color: 'red' },
  8: { label: '管理员', color: 'volcano' },
  7: { label: '租户', color: 'geekblue' },
  3: { label: '设备管理员', color: 'green' },
  2: { label: '清运员', color: 'cyan' },
  1: { label: '普通用户', color: 'default' },
};

/** 平台域角色码（用于路由守卫/视图判定） */
export const ROLE_SUPER_ADMIN = 9;
export const ROLE_ADMIN = 8;
export const ROLE_TENANT = 7;

/** 通用状态：账号/租户启用 */
export const STATUS: EnumMap = {
  0: { label: '禁用', color: 'default' },
  1: { label: '启用', color: 'success' },
};

/** 设备状态（0 离线 / 1 在线 / 2 维护中） */
export const DEVICE_STATUS: EnumMap = {
  0: { label: '离线', color: 'default' },
  1: { label: '在线', color: 'success' },
  2: { label: '维护中', color: 'warning' },
};

/** 设备类型 type */
export const DEVICE_TYPE: EnumMap = {
  0: { label: '通用' },
  1: { label: '智能垃圾箱' },
  2: { label: '滚动系统' },
};

/** 投口启用 enabled */
export const ENABLED: EnumMap = {
  0: { label: '禁用', color: 'default' },
  1: { label: '启用', color: 'success' },
};

/** 垃圾一级分类 wasteType1 */
export const WASTE_TYPE1: EnumMap = {
  0: { label: '无', color: 'default' },
  1: { label: '厨余垃圾', color: 'green' },
  2: { label: '可回收垃圾', color: 'blue' },
  3: { label: '有害垃圾', color: 'red' },
  4: { label: '其他垃圾', color: 'default' },
};

/** 垃圾二级分类 wasteType2 */
export const WASTE_TYPE2: EnumMap = {
  0: { label: '不区分', color: 'default' },
  1: { label: '纸类', color: 'gold' },
  2: { label: '塑料', color: 'blue' },
  3: { label: '织物', color: 'purple' },
  4: { label: '金属', color: 'cyan' },
  5: { label: '其他', color: 'default' },
};

/** 登录方式 loginType */
export const LOGIN_TYPE: EnumMap = {
  0: { label: '未知' },
  1: { label: '手机号' },
  2: { label: 'IC 卡' },
  3: { label: '人脸识别' },
  4: { label: '二维码' },
  5: { label: '微信小程序' },
};

/** 投递阶段 deliveryStatus */
export const DELIVERY_STATUS: EnumMap = {
  0: { label: '进行中', color: 'processing' },
  1: { label: '已完成', color: 'success' },
};

/** 投递异常标记 status */
export const DELIVERY_ABNORMAL: EnumMap = {
  0: { label: '正常', color: 'success' },
  [-1]: { label: '异常', color: 'error' },
};

/** 清运审核状态 auditStatus */
export const AUDIT_STATUS: EnumMap = {
  0: { label: '待审核', color: 'processing' },
  1: { label: '审核通过', color: 'success' },
  2: { label: '审核拒绝', color: 'error' },
};

/** 清运单状态 status */
export const CLEAN_STATUS: EnumMap = {
  0: { label: '创建', color: 'default' },
  1: { label: '完成', color: 'success' },
  2: { label: '取消', color: 'warning' },
};

/** 提现单状态 status */
export const WITHDRAW_STATUS: EnumMap = {
  0: { label: '待审核', color: 'processing' },
  1: { label: '已通过', color: 'success' },
  2: { label: '已驳回', color: 'error' },
};

/** 把 EnumMap 转成 ProTable valueEnum（{ [key]: { text } }） */
export function toValueEnum(map: EnumMap): Record<string, { text: string }> {
  const out: Record<string, { text: string }> = {};
  Object.entries(map).forEach(([k, v]) => {
    out[k] = { text: v.label };
  });
  return out;
}

/** 把 EnumMap 转成 ProForm/Select options（{ label, value }[]） */
export function toOptions(map: EnumMap): { label: string; value: number }[] {
  return Object.entries(map).map(([k, v]) => ({ label: v.label, value: Number(k) }));
}

/** 统计接口返回 Map 的英文 key → 中文标签（key 由真实联调采集，见各 /statistics/*） */
export const STAT_LABELS: Record<string, string> = {
  // dashboard / today-overview
  deliveryCount: '投递次数',
  totalWeight: '总重量(kg)',
  todayMemberCount: '今日新增会员',
  // devices
  totalCount: '设备总数',
  onlinkCount: '在线设备数',
  spillCount: '溢满设备数',
  smokeCount: '烟感告警数',
  // members
  memberCount: '会员总数',
  memberDisableCount: '禁用会员数',
  // delivery
  deliveryWeight: '投递重量(kg)',
  deliveryMoney: '投递返现(元)',
  minusyMoney: '扣减金额(元)',
  // clean
  cleanCount: '清运次数',
  totalWeights: '清运总重量(kg)',
  storageWeights: '在库重量(kg)',
  minusyWeights: '清出重量(kg)',
  // payout
  payOutCount: '支出笔数',
  payOutMoney: '支出总额(元)',
  pushSuccessMoney: '转账成功金额(元)',
  refundUserMoney: '退款金额(元)',
  // member-money
  memberMoney: '会员余额(元)',
  memberScore: '会员积分',
  memberPlanMoney: '会员待结金额(元)',
};

/** 统计 key → 中文标签；未知 key 原样返回 */
export function statLabel(key: string): string {
  return STAT_LABELS[key] ?? key;
}
