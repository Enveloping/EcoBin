import type {
  CleanOperationStatus,
  CleanOptionBlocker,
  CleanPortOption,
} from '../types/api'

const BLOCKER_TEXT: Record<CleanOptionBlocker, string> = {
  CLEAN_CONFIGURATION_UNAVAILABLE: '机构尚未配置清运规则',
  CONFIGURATION_NOT_APPLIED: '设备配置尚未完整应用',
  DEVICE_SOFTWARE_NOT_ACCEPTING: '设备正在维护，请稍后再发起清运',
  EDGE_OFFLINE: '设备当前未确认联网',
  DEVICE_BUSY: '设备正在执行其他作业',
  PORT_DISABLED: '当前投口未启用清运',
  CLEAN_OPERATION_ACTIVE: '该投口已有未结束清运',
  PORT_WORK_ACTIVE: '投口正在检测或测量空袋基准',
}

export type CleanOperationDisposition =
  | 'POLL'
  | 'COMPLETED'
  | 'PRE_UNLOCK_ENDED'
  | 'RECOVERY_REQUIRED'
  | 'ABORTED'
  | 'UNKNOWN'

export function cleanBlockerText(blocker: string): string {
  return BLOCKER_TEXT[blocker as CleanOptionBlocker]
    || '暂时无法清运，请稍后重试或联系管理员'
}

export function autoSelectedCleanPortNo(
  ports: CleanPortOption[],
): number | null {
  const available = ports.filter((port) => port.cleaningAllowed)
  return available.length === 1 ? available[0].portNo : null
}

export function cleanOperationDisposition(
  status: string | null | undefined,
): CleanOperationDisposition {
  switch (status as CleanOperationStatus) {
    case 'PREPARED':
    case 'EDGE_SAVED':
    case 'IN_PROGRESS':
      return 'POLL'
    case 'COMPLETED':
      return 'COMPLETED'
    case 'PRE_UNLOCK_ENDED':
      return 'PRE_UNLOCK_ENDED'
    case 'RECOVERY_REQUIRED':
      return 'RECOVERY_REQUIRED'
    case 'ABORTED':
      return 'ABORTED'
    default:
      return 'UNKNOWN'
  }
}
