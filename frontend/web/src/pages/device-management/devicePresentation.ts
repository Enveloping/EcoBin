import type {
  DeviceAcceptanceStatus,
  DeviceAssetLifecycleStatus,
  DeviceConfigurationApplicationStatus,
} from '@/api/deviceDirectory';

export const assetLabels: Record<DeviceAssetLifecycleStatus, string> = {
  NORMAL: '正常',
  DISABLED: '已禁用',
  RETIRED: '已报废',
};

export const assetColors: Record<DeviceAssetLifecycleStatus, string> = {
  NORMAL: 'success',
  DISABLED: 'warning',
  RETIRED: 'default',
};

export const acceptanceLabels: Record<DeviceAcceptanceStatus, string> = {
  PENDING: '设备功能检查尚未完成',
  FAILED: '设备功能检查未通过',
  PASSED: '设备功能检查通过',
};

export const acceptanceColors: Record<DeviceAcceptanceStatus, string> = {
  PENDING: 'processing',
  FAILED: 'error',
  PASSED: 'success',
};

export const connectivityLabels = {
  ONLINE: '在线',
  OFFLINE: '离线',
  UNKNOWN: '状态未知',
} as const;

export const connectivityColors = {
  ONLINE: 'success',
  OFFLINE: 'error',
  UNKNOWN: 'default',
} as const;

const runtimeLabels: Record<string, string> = {
  ONLINE: '在线',
  OFFLINE: '离线',
  UNKNOWN: '未知',
  OK: '正常',
  SAFE: '安全',
  READY: '就绪',
  NORMAL: '正常',
  CLEAR: '无遮挡',
  BLOCKED: '有遮挡',
  CLOSED: '关闭',
  OPEN: '打开',
  OPENING: '正在打开',
  CLOSING: '正在关闭',
  NONE: '无命令',
  CLOSE: '关闭',
  DEENERGIZED: '未通电',
  ENERGIZED: '已通电',
  STABLE: '稳定',
  UNSTABLE: '不稳定',
  DEGRADED: '降级',
  FAILED: '故障',
  FAULT: '故障',
  ACTUATOR_FAULT: '驱动机构故障',
  SWITCH_FAULT: '限位开关故障',
  DRIVER_FAULT: '驱动故障',
  SENSOR_FAULT: '传感器故障',
  PROTOCOL_ERROR: '通信格式错误',
  CONFIG_ERROR: '配置错误',
  OVERLOAD: '超量程',
  INCOMPATIBLE: '不兼容',
  DISCONNECTED: '未连接',
  NEGOTIATING: '正在协商',
  TIMEOUT: '超时',
  JAMMED: '卡滞',
  UNAVAILABLE: '不可获取',
  ALARM: '报警',
  OPERATION_BLOCKED: '作业阻断',
  SAFETY_BLOCKED: '安全阻断',
  DELIVERY: '投递占用',
  CLEAN: '清运占用',
  INFERRED_FROM_LOCK_POWER: '根据锁供电推断',
  NOT_OBSERVABLE: '硬件不可直接观测',
  CLEANER_CONFIRMATION: '清运员现场确认',
  NOT_DISPATCHED: '未下发',
  COMMAND_DISPATCHED: '命令已下发',
  COMMAND_SUPERSEDED_BEFORE_DISPATCH: '下发前已被新命令替代',
  COALESCED_WITH_EXISTING_CLOSE: '已合并到现有关闭命令',
  OUTPUT_REJECTED: '命令输出被拒绝',
  STABLE_WINDOW_MEAN: '稳定窗口均值',
  LAST_FOUR_MEAN: '最近四次均值',
  AVAILABLE_SAMPLES_MEAN: '有效样本均值',
  LAST_OBSERVED: '最近观测值',
  ULTRASONIC: '超声波',
  DIGITAL_INFRARED: '数字红外',
  NOT_SAMPLED: '本次未采样',
};

export function runtimeStatusLabel(value?: string | null): string {
  if (!value) return '尚无数据';
  return runtimeLabels[value] ?? '状态暂不支持显示';
}

export function runtimeStatusColor(value?: string | null): string {
  if (!value || value === 'UNKNOWN') return 'default';
  if ([
    'ONLINE', 'OK', 'SAFE', 'READY', 'NORMAL', 'CLEAR', 'CLOSED',
    'DEENERGIZED', 'STABLE',
  ].includes(value)) return 'success';
  if ([
    'OFFLINE', 'FAILED', 'FAULT', 'ACTUATOR_FAULT', 'SWITCH_FAULT',
    'DRIVER_FAULT', 'SENSOR_FAULT', 'PROTOCOL_ERROR', 'CONFIG_ERROR',
    'OVERLOAD', 'INCOMPATIBLE', 'DISCONNECTED', 'JAMMED', 'ALARM',
    'SAFETY_BLOCKED', 'OUTPUT_REJECTED',
  ].includes(value)) return 'error';
  if ([
    'DEGRADED', 'UNSTABLE', 'TIMEOUT', 'BLOCKED', 'OPERATION_BLOCKED',
    'ENERGIZED', 'UNAVAILABLE', 'NEGOTIATING',
  ].includes(value)) return 'warning';
  if (['OPEN', 'OPENING', 'CLOSING', 'DELIVERY', 'CLEAN'].includes(value)) {
    return 'processing';
  }
  return 'warning';
}

export function technicalIssueStateLabel(value: string): string {
  return ({
    AUTO_RETRYING: '系统正在自动处理',
    ACTION_REQUIRED: '需要管理员处理',
    RECOVERY_REQUIRED: '需要现场安全恢复',
    RECOVERY_IN_PROGRESS: '正在等待设备安全确认',
    RECORDED: '已隔离并留档',
  } as Record<string, string>)[value] ?? '处理状态待确认';
}

export function operatorFacingTechnicalText(value: string): string {
  const replacements = [
    ['P7', '本机硬件检查'],
    ['P8', '云端自动验收'],
    ['机器验收', '设备功能检查'],
    ['OneNet', '云端设备平台'],
    ['MQTT', '云端连接'],
    ['MCU', '设备控制板'],
    ['COS', '云端照片存储'],
    ['UART', '控制板通信'],
    ['K1', '一次性注册密钥'],
    ['验收证据', '验收记录'],
    ['设备证据', '设备确认结果'],
    ['可靠任务', '后台处理任务'],
    ['重放原', '重复发送原'],
    ['代际', '轮次'],
  ] as const;
  const translated = replacements.reduce(
    (text, [internal, friendly]) => text.split(internal).join(friendly),
    value,
  );
  return translated.replace(
    /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g,
    '技术问题',
  );
}

export const configurationLabels: Record<
  DeviceConfigurationApplicationStatus,
  string
> = {
  PENDING: '自动下发中',
  EDGE_SAVED: '设备已保存',
  APPLIED: '已精确应用',
  FAILED: '设备拒绝或应用失败',
};

export const configurationColors: Record<
  DeviceConfigurationApplicationStatus,
  string
> = {
  PENDING: 'processing',
  EDGE_SAVED: 'cyan',
  APPLIED: 'success',
  FAILED: 'error',
};

export const evidenceLabels: Record<string, string> = {
  PASSED: '通过',
  FAILED: '未通过',
  PENDING: '待判断',
};

export function booleanEvidence(value: boolean): string {
  return value ? '正常' : '异常';
}
