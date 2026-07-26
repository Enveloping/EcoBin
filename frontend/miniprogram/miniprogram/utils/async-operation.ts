import { STORAGE_KEYS } from '../config/index'
import { request } from './request'

export interface AcceptedOperation {
  operationId: string
  resourceId: string
  status: 'ACCEPTED' | 'PROCESSING' | 'UNKNOWN'
  statusUrl: string
  recommendedPollAfterMs: number
}

export interface PollableProjection {
  status: string
  recommendedPollAfterMs?: number
}

export interface PollOptions<T extends PollableProjection> {
  operation: AcceptedOperation
  isTerminal: (projection: T) => boolean
  onUpdate?: (projection: T) => void
  isCancelled?: () => boolean
  maximumElapsedMs?: number
}

const STATUS_URL = /^\/api\/v1\/(miniapp|miniapp-staff)\//

function storageKey(resourceId: string): string {
  return `${STORAGE_KEYS.pendingOperationPrefix}${resourceId}`
}

function validateStatusUrl(statusUrl: string): void {
  if (!STATUS_URL.test(statusUrl)) {
    throw new TypeError('statusUrl must be a same-origin miniapp /api/v1 path')
  }
}

function pollDelay(value: number | undefined): number {
  if (!Number.isFinite(value)) return 1500
  return Math.min(60000, Math.max(250, Math.trunc(value as number)))
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

export function rememberAcceptedOperation(
  operation: AcceptedOperation,
): void {
  validateStatusUrl(operation.statusUrl)
  wx.setStorageSync(storageKey(operation.resourceId), operation)
}

export function restoreAcceptedOperation(
  resourceId: string,
): AcceptedOperation | null {
  const value = wx.getStorageSync(storageKey(resourceId)) as unknown
  if (!value || typeof value !== 'object') return null
  try {
    const operation = value as AcceptedOperation
    if (operation.resourceId !== resourceId) throw new Error('resource mismatch')
    validateStatusUrl(operation.statusUrl)
    return operation
  } catch {
    wx.removeStorageSync(storageKey(resourceId))
    return null
  }
}

export function forgetAcceptedOperation(resourceId: string): void {
  wx.removeStorageSync(storageKey(resourceId))
}

export async function pollAcceptedOperation<T extends PollableProjection>(
  options: PollOptions<T>,
): Promise<T> {
  const { operation, isTerminal, onUpdate, isCancelled } = options
  validateStatusUrl(operation.statusUrl)
  rememberAcceptedOperation(operation)
  const startedAt = Date.now()
  const maximumElapsedMs = options.maximumElapsedMs ?? 5 * 60 * 1000
  let waitMs = pollDelay(operation.recommendedPollAfterMs)

  while (true) {
    await delay(waitMs)
    if (isCancelled?.()) throw new Error('异步状态查询已取消')
    const projection = await request<T>({
      url: operation.statusUrl,
      method: 'GET',
      noStore: true,
      toast: false,
    })
    onUpdate?.(projection)
    if (isTerminal(projection)) {
      forgetAcceptedOperation(operation.resourceId)
      return projection
    }
    if (Date.now() - startedAt >= maximumElapsedMs) {
      throw new Error('异步状态仍未结束，可稍后按资源编号继续查询')
    }
    waitMs = pollDelay(projection.recommendedPollAfterMs)
  }
}
