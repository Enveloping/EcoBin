import { getSession } from '../utils/auth'
import { http, requestAccepted } from '../utils/request'
import type {
  CleanOperationAccepted,
  CleanOperationView,
  CleanOptionsView,
  CleanRecordItem,
  CursorPage,
  MiniappCleanRecordDetail,
} from '../types/api'

function requireRealCleaningSession(): void {
  const session = getSession()
  if (session?.audience !== 'miniapp' || session.entryMode !== 'CLEANING') {
    throw new Error('当前登录会话不能请求清运数据')
  }
}

export function cleanOptions(deploymentCode: string, toast = true) {
  requireRealCleaningSession()
  return http.get<CleanOptionsView>(
    `/api/v1/miniapp/device-deployments/${
      encodeURIComponent(deploymentCode)
    }/clean-options`,
    undefined,
    { toast, noStore: true },
  )
}

export function startCleanOperation(
  deploymentCode: string,
  portNo: number,
  installedBagQr: string,
  idempotencyKey: string,
) {
  requireRealCleaningSession()
  return requestAccepted<CleanOperationAccepted>({
    url:
      `/api/v1/miniapp/device-deployments/${
        encodeURIComponent(deploymentCode)
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
