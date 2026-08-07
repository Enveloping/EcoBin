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
  PENDING: '等待真实设备证据',
  FAILED: '证据未通过',
  PASSED: '机器验收通过',
};

export const acceptanceColors: Record<DeviceAcceptanceStatus, string> = {
  PENDING: 'processing',
  FAILED: 'error',
  PASSED: 'success',
};

export const configurationLabels: Record<
  DeviceConfigurationApplicationStatus,
  string
> = {
  PENDING: '自动下发中',
  EDGE_SAVED: '香橙派已保存',
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
