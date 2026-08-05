import type {
  DeviceAssetLifecycleStatus,
  DeviceConfigurationApplicationStatus,
  DeviceDeploymentLifecycleStatus,
  DeviceTenantAllocationStatus,
} from '@/api/deviceDirectory';

export const assetLabels: Record<DeviceAssetLifecycleStatus, string> = {
  IN_STOCK: '平台库存',
  ALLOCATED: '已分配租户',
  IN_USE: '机构使用中',
  MAINTENANCE: '维修隔离',
  RETIRED: '已退役',
};

export const assetColors: Record<DeviceAssetLifecycleStatus, string> = {
  IN_STOCK: 'success',
  ALLOCATED: 'processing',
  IN_USE: 'cyan',
  MAINTENANCE: 'warning',
  RETIRED: 'default',
};

export const allocationLabels: Record<
  DeviceTenantAllocationStatus,
  string
> = {
  ACTIVE: '当前有效',
  ENDED: '已结束',
};

export const allocationColors: Record<
  DeviceTenantAllocationStatus,
  string
> = {
  ACTIVE: 'success',
  ENDED: 'default',
};

export const lifecycleLabels: Record<
  DeviceDeploymentLifecycleStatus,
  string
> = {
  PENDING_INSTALL: '待安装',
  COMMISSIONING: '调试中',
  ENABLED: '已启用',
  MAINTENANCE: '维护中',
  DISABLED: '已停用',
  ENDED: '已结束',
};

export const lifecycleColors: Record<
  DeviceDeploymentLifecycleStatus,
  string
> = {
  PENDING_INSTALL: 'default',
  COMMISSIONING: 'processing',
  ENABLED: 'success',
  MAINTENANCE: 'warning',
  DISABLED: 'default',
  ENDED: 'default',
};

export const configurationLabels: Record<
  DeviceConfigurationApplicationStatus,
  string
> = {
  PENDING: '等待设备证明',
  EDGE_SAVED: '边缘已保存',
  APPLIED: '已应用',
  FAILED: '设备应用失败',
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

export const dispatchLabels: Record<string, string> = {
  PENDING: '等待下发',
  RUNNING: '正在下发',
  DONE: '任务已完成',
  BLOCKED: '下发已阻塞',
  CANCELLED: '任务已取消',
};

export const connectionStatusLabels: Record<string, string> = {
  ONLINE: '在线',
  OFFLINE: '离线',
  UNKNOWN: '未知',
};

export const blockerLabels: Record<string, string> = {
  TENANT_DISABLED: '租户已停用',
  ORGANIZATION_DISABLED: '机构已停用',
  DEPLOYMENT_NOT_ENABLED: '部署尚未启用',
  BUSINESS_SWITCH_DISABLED: '经营开关已关闭',
  CONFIGURATION_NOT_APPLIED: '最新配置尚未应用',
  EDGE_OFFLINE: '设备业务在线状态为离线',
  DEVICE_OFFLINE: 'OneNet 显示设备离线，可靠任务等待设备上线',
  DEVICE_IDENTITY_UNRESOLVED: 'OneNet 产品下找不到该设备身份',
  MCU_OFFLINE: 'MCU 离线',
  PROTOCOL_INCOMPATIBLE: '设备协议不兼容',
  SAFETY_LOCKED: '设备存在安全锁',
  DOOR_NOT_CLOSED: '投递门未确认关闭',
  PORT_DISABLED: '投口已停用',
  PORT_SENSOR_UNHEALTHY: '投口传感器异常',
  PORT_FULL: '投口已满',
  CURRENT_BAG_MISSING: '当前袋缺失',
  WEIGHT_BASELINE_MISSING: '重量基准缺失',
  BASELINE_REMEASUREMENT_ACTIVE: '正在重测重量基准',
  PORT_CLEAN_OPERATION_ACTIVE: '投口正在清运',
  DEVICE_BUSY: '整机正在执行其他作业',
  INITIAL_COMMISSIONING: '尚未完成首次调试验收',
  BUSINESS_STILL_ENABLED: '经营开关尚未关闭',
  ACTIVE_WORK_EXISTS: '存在未结束的投递或清运作业',
  EDGE_RUNTIME_STALE: '设备运行事实已过期',
  PENDING_RELIABLE_EVENTS: '设备还有待上传的可靠事件',
  PHYSICAL_POSSESSION_UNCONFIRMED: '尚未确认平台已收回设备实物',
  DELIVERY_SESSION_ACTIVE: '存在未结束的投递会话',
  SERIOUS_FAULT_OPEN: '设备存在未恢复的严重故障',
  CONFIGURATION_VERSION_MISMATCH: '设备应用的配置版本不精确',
  RUNTIME_NOT_RECEIVED: '尚未收到可信运行事实',
  RUNTIME_TOO_OLD: '可信运行事实已超时',
  TRUSTED_RUNTIME_STALE: '可信运行事实已超时',
  MCU_COMMUNICATION_NOT_READY: 'MCU 通信尚未就绪',
  WEIGHT_SENSOR_UNHEALTHY: '称重传感器异常',
  CAMERA_UNHEALTHY: '摄像头异常',
  LOCAL_STORAGE_UNHEALTHY: '香橙派本地存储异常',
  CLOCK_NOT_SYNCHRONIZED: '设备时钟尚未同步',
  PORT_RUNTIME_INCOMPLETE: '投口运行证据不完整',
  PORT_RUNTIME_NOT_FROM_LATEST_SNAPSHOT: '投口证据不属于最新运行快照',
  CLEAN_LOCK_UNHEALTHY: '清运锁或电磁阀异常',
  DIGITAL_INFRARED_UNHEALTHY: '数字红外传感器异常',
  SMOKE_SENSOR_UNHEALTHY: '烟雾传感器异常',
  PORT_SAFETY_FAULT: '投口存在安全故障',
  ONENET_CREDENTIAL_ROTATION_REQUIRED: '需要先线下轮换 OneNet Device Key',
  DELIVERY_DOOR_MANUAL_CONFIRMATION_REQUIRED:
    '需要人工确认投递门安装正常',
  CAMERA_MANUAL_CONFIRMATION_REQUIRED:
    '需要人工确认摄像头安装正常',
  CLEAN_DOOR_MANUAL_CONFIRMATION_REQUIRED:
    '需要人工确认清运门安装正常',
};

export function connectionStatusColor(
  status: string | null | undefined,
): string {
  if (status === 'ONLINE') return 'success';
  if (status === 'OFFLINE') return 'error';
  return 'default';
}

export function connectionStatusLabel(
  status: string | null | undefined,
): string {
  if (!status) return '未上报';
  return connectionStatusLabels[status] ?? status;
}

export function healthColor(status: string | null | undefined): string {
  if (!status || ['UNKNOWN', 'NOT_SAMPLED'].includes(status)) return 'default';
  if (
    ['ONLINE', 'HEALTHY', 'NORMAL', 'SAFE', 'CLOSED', 'OFF'].includes(status)
  ) {
    return 'success';
  }
  if (['DEGRADED', 'WARNING'].includes(status)) return 'warning';
  return 'error';
}

export function blockerLabel(code: string): string {
  return blockerLabels[code] ?? code;
}
