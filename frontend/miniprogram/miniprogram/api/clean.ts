import { getSession } from '../utils/auth'
import { http, requestAccepted } from '../utils/request'
import type {
  CleanOperationAccepted,
  CleanOperationView,
  CleanOptionsView,
  CleanDeviceFilter,
  CleanDeviceItem,
  CleanRecordItem,
  CursorPage,
  MiniappCleanRecordDetail,
  DeviceInstallationProfile,
  UpdateDeviceInstallationProfileRequest,
  InterruptedCleanBagRecoveryAccepted,
  RecoverInterruptedCleanBagRequest,
} from '../types/api'

function requireRealCleaningSession(): void {
  const session = getSession()
  if (session?.audience !== 'miniapp' || session.entryMode !== 'CLEANING') {
    throw new Error('当前登录会话不能请求清运数据')
  }
}

export function myCleanDevices(
  options: {
    filter: CleanDeviceFilter
    cursor?: string
    limit?: number
  },
  toast = true,
) {
  requireRealCleaningSession()
  return http.get<CursorPage<CleanDeviceItem>>(
    '/api/v1/miniapp/me/clean-devices',
    options,
    { toast, noStore: true },
  )
}

export function cleanOptions(deviceCode: string, toast = true) {
  requireRealCleaningSession()
  return http.get<CleanOptionsView>(
    `/api/v1/miniapp/devices/${encodeURIComponent(deviceCode)}/clean-options`,
    undefined,
    { toast, noStore: true },
  )
}

export function deviceInstallationProfile(
  deviceCode: string,
  toast = true,
) {
  requireRealCleaningSession()
  return http.get<DeviceInstallationProfile>(
    `/api/v1/miniapp/devices/${
      encodeURIComponent(deviceCode)
    }/installation-profile`,
    undefined,
    { toast, noStore: true },
  )
}

export function updateDeviceInstallationProfile(
  deviceCode: string,
  body: UpdateDeviceInstallationProfileRequest,
  toast = true,
) {
  requireRealCleaningSession()
  return http.put<DeviceInstallationProfile>(
    `/api/v1/miniapp/devices/${
      encodeURIComponent(deviceCode)
    }/installation-profile`,
    body as unknown as Record<string, unknown>,
    { toast, noStore: true },
  )
}

export function startCleanOperation(
  deviceCode: string,
  portNo: number,
  installedBagQr: string,
  idempotencyKey: string,
) {
  requireRealCleaningSession()
  return requestAccepted<CleanOperationAccepted>({
    url:
      `/api/v1/miniapp/devices/${
        encodeURIComponent(deviceCode)
      }/ports/${portNo}/clean-operations`,
    method: 'POST',
    data: { installedBagQr },
    idempotencyKey,
    toast: false,
  })
}

export function cleanOperation(operationUid: string, toast = false) {
  requireRealCleaningSession()
  return http.get<CleanOperationView>(
    `/api/v1/miniapp/clean-operations/${encodeURIComponent(operationUid)}`,
    undefined,
    { toast, noStore: true },
  )
}

export function recoverInterruptedCleanBag(
  operationUid: string,
  body: RecoverInterruptedCleanBagRequest,
  idempotencyKey: string,
) {
  requireRealCleaningSession()
  return http.post<InterruptedCleanBagRecoveryAccepted>(
    `/api/v1/miniapp/clean-operations/${
      encodeURIComponent(operationUid)
    }/bag-recoveries`,
    body as unknown as Record<string, unknown>,
    { idempotencyKey, toast: false, noStore: true },
  )
}

export function myCleanRecords(
  options: { cursor?: string; limit?: number } = {},
  toast = true,
) {
  requireRealCleaningSession()
  return http.get<CursorPage<CleanRecordItem>>(
    '/api/v1/miniapp/me/clean-records',
    options,
    { toast, noStore: true },
  )
}

export function cleanRecordDetail(cleanRecordNo: string, toast = true) {
  requireRealCleaningSession()
  return http.get<MiniappCleanRecordDetail>(
    `/api/v1/miniapp/me/clean-records/${
      encodeURIComponent(cleanRecordNo)
    }`,
    undefined,
    { toast, noStore: true },
  )
}
