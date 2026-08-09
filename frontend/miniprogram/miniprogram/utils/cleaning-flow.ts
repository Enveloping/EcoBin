import type {
  CleanOperationStatus,
  CleanOptionBlocker,
  CleanPortOption,
} from '../types/api'

const BLOCKER_TEXT: Record<CleanOptionBlocker, string> = {
  DEVICE_BUSY: '设备正在执行其他作业',
  CLEAN_OPERATION_ACTIVE: '该投口已有未结束清运',
  CLEAN_LOCK_NOT_SAFE: '清运电磁锁状态不安全',
  CLEAN_SOLENOID_UNAVAILABLE: '清运电磁阀不可用',
  WEIGHT_UNAVAILABLE: '称重状态不可用',
  SAFETY_UNAVAILABLE: '设备安全状态不可用',
  DEVICE_FAULT_ACTIVE: '设备存在未恢复故障',
}

export type CleanOperationDisposition =
  | 'POLL'
  | 'COMPLETED'
  | 'PRE_UNLOCK_ENDED'
  | 'RECOVERY_REQUIRED'
  | 'ABORTED'
  | 'UNKNOWN'

export function cleanBlockerText(blocker: CleanOptionBlocker): string {
  return BLOCKER_TEXT[blocker]
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
