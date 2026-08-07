import { STORAGE_KEYS } from '../config/index'
import type {
  DeliverySessionAccepted,
  LoginResponse,
} from '../types/api'
import {
  parseDeviceLinkUrl,
  parseOrdinaryDeviceLink,
  type DeviceLink,
} from './ordinary-device-link'

export type DeviceEntrySource = 'ORDINARY_LINK' | 'IN_APP_SCAN'

export interface PendingDeviceEntry extends DeviceLink {
  entryId: string
  fingerprint: string
  source: DeviceEntrySource
  capturedAt: number
  scanCodeTime?: string
  selectedPortNo?: number
  idempotencyKey?: string
  startAttemptCount?: number
  lastStartAttemptAt?: number
  accepted?: DeliverySessionAccepted
}

interface LastHandledDeviceEntry {
  fingerprint: string
  handledAt: number
  hasScanCodeTime: boolean
}

type EntryQuery = Record<string, unknown>

const PENDING_TTL_MS = 30 * 60 * 1000
const ACCEPTED_TTL_MS = 24 * 60 * 60 * 1000
const NO_SCAN_TIME_SUPPRESSION_MS = 24 * 60 * 60 * 1000
let entrySequence = 0
let routingPendingEntry = false
let routeBlockedUntil = 0
let pendingClearedLocally = false

function readStored<T>(key: string): T | undefined {
  try {
    const value = wx.getStorageSync(key) as unknown
    return value && typeof value === 'object' ? value as T : undefined
  } catch {
    return undefined
  }
}

function writePending(
  entry: PendingDeviceEntry,
): PendingDeviceEntry | undefined {
  try {
    wx.setStorageSync(STORAGE_KEYS.pendingDeviceEntry, entry)
    pendingClearedLocally = false
    return entry
  } catch {
    return undefined
  }
}

function removePending(): void {
  pendingClearedLocally = true
  try {
    wx.removeStorageSync(STORAGE_KEYS.pendingDeviceEntry)
  } catch {
    try {
      // 删除失败时以不可被识别为入口的墓碑覆盖，避免跨账号恢复旧意图。
      wx.setStorageSync(STORAGE_KEYS.pendingDeviceEntry, {
        clearedAt: Date.now(),
      })
    } catch {
      // 本地缓存完全不可用时，当前页面执行锁仍会阻止同实例重复提交。
    }
  }
}

function queryString(
  query: EntryQuery,
  key: string,
): string | undefined {
  const value = query[key]
  return typeof value === 'string' ? value : undefined
}

function scanCodeTime(query: EntryQuery): string | undefined {
  const value = queryString(query, 'scancode_time')?.trim()
  return value && /^\d{1,16}$/.test(value) ? value : undefined
}

function fingerprint(
  link: DeviceLink,
  source: DeviceEntrySource,
  scanTime: string | undefined,
  capturedAt: number,
): string {
  const occurrence = scanTime ?? (
    source === 'IN_APP_SCAN'
      ? String(capturedAt)
      : 'NO_SCAN_TIME'
  )
  return [
    source,
    link.deviceCode,
    occurrence,
  ].join('|')
}

function newEntry(
  link: DeviceLink,
  source: DeviceEntrySource,
  scanTime?: string,
): PendingDeviceEntry {
  const capturedAt = Date.now()
  entrySequence = (entrySequence + 1) % 100000
  return {
    ...link,
    entryId: `${capturedAt.toString(36)}-${entrySequence.toString(36)}`,
    fingerprint: fingerprint(link, source, scanTime, capturedAt),
    source,
    capturedAt,
    scanCodeTime: scanTime,
  }
}

function isExpired(entry: PendingDeviceEntry): boolean {
  const ttl = entry.accepted || entry.idempotencyKey
    ? ACCEPTED_TTL_MS
    : PENDING_TTL_MS
  return !Number.isFinite(entry.capturedAt)
    || Date.now() - entry.capturedAt > ttl
}

function isSuppressed(entryFingerprint: string): boolean {
  const handled = readStored<LastHandledDeviceEntry>(
    STORAGE_KEYS.lastHandledDeviceEntry,
  )
  if (!handled || handled.fingerprint !== entryFingerprint) return false
  if (handled.hasScanCodeTime) return true
  return Date.now() - handled.handledAt < NO_SCAN_TIME_SUPPRESSION_MS
}

function rememberHandled(entry: PendingDeviceEntry): void {
  try {
    wx.setStorageSync(STORAGE_KEYS.lastHandledDeviceEntry, {
      fingerprint: entry.fingerprint,
      handledAt: Date.now(),
      hasScanCodeTime: !!entry.scanCodeTime,
    } satisfies LastHandledDeviceEntry)
  } catch {
    // 抑制记录失败不能阻止 pending 被清理。
  }
}

function capture(
  link: DeviceLink,
  source: DeviceEntrySource,
  scanTime?: string,
): PendingDeviceEntry | undefined {
  const existing = peekPendingDeviceEntry()
  if (existing?.accepted || existing?.idempotencyKey) return existing

  const candidate = newEntry(link, source, scanTime)
  if (existing?.fingerprint === candidate.fingerprint) return existing
  if (isSuppressed(candidate.fingerprint)) return undefined
  return writePending(candidate)
}

export function captureOrdinaryDeviceEntryFromQuery(
  query: EntryQuery,
): PendingDeviceEntry | undefined {
  const link = parseOrdinaryDeviceLink(queryString(query, 'q'))
  return link
    ? capture(link, 'ORDINARY_LINK', scanCodeTime(query))
    : undefined
}

export function captureOrdinaryDeviceEntry(
  options: WechatMiniprogram.LaunchOptionsApp,
): PendingDeviceEntry | undefined {
  return captureOrdinaryDeviceEntryFromQuery(
    options.query as EntryQuery,
  )
}

export function captureScannedDeviceEntry(
  rawLink: string,
): PendingDeviceEntry | undefined {
  const link = parseDeviceLinkUrl(rawLink)
  return link ? capture(link, 'IN_APP_SCAN') : undefined
}

export function peekPendingDeviceEntry(): PendingDeviceEntry | undefined {
  if (pendingClearedLocally) return undefined
  const entry = readStored<PendingDeviceEntry>(
    STORAGE_KEYS.pendingDeviceEntry,
  )
  if (!entry) return undefined
  if (
    !entry.entryId
    || !entry.fingerprint
    || !entry.deviceCode
    || isExpired(entry)
  ) {
    removePending()
    return undefined
  }
  return entry
}

export function preparePendingDeviceStart(
  entryId: string,
  portNo: number,
  idempotencyKey: string,
): PendingDeviceEntry | undefined {
  const entry = peekPendingDeviceEntry()
  if (!entry || entry.entryId !== entryId || entry.accepted) return undefined
  if (
    entry.selectedPortNo === portNo
    && entry.idempotencyKey
  ) {
    return entry
  }
  return writePending({
    ...entry,
    selectedPortNo: portNo,
    idempotencyKey,
    startAttemptCount: 0,
  })
}

/** 服务端明确拒绝（4xx）时释放准备态；未知网络结果不得调用。 */
export function releasePendingDeviceStart(
  entryId: string,
  portNo: number,
  idempotencyKey: string,
  expectedAttemptCount: number,
): PendingDeviceEntry | undefined {
  const entry = peekPendingDeviceEntry()
  if (
    !entry
    || entry.entryId !== entryId
    || entry.selectedPortNo !== portNo
    || entry.idempotencyKey !== idempotencyKey
    || entry.startAttemptCount !== expectedAttemptCount
    || entry.accepted
  ) {
    return undefined
  }
  const released: PendingDeviceEntry = { ...entry }
  delete released.selectedPortNo
  delete released.idempotencyKey
  delete released.startAttemptCount
  delete released.lastStartAttemptAt
  return writePending(released)
}

/** 必须在真正发出 POST 前同步持久化，用于区分首次明确拒绝与未知结果恢复。 */
export function markPendingDeviceStartAttempt(
  entryId: string,
): PendingDeviceEntry | undefined {
  const entry = peekPendingDeviceEntry()
  if (
    !entry
    || entry.entryId !== entryId
    || !entry.idempotencyKey
    || entry.accepted
  ) {
    return undefined
  }
  const previousAttempts = Number.isInteger(entry.startAttemptCount)
    && (entry.startAttemptCount as number) >= 0
    ? entry.startAttemptCount as number
    // 兼容旧缓存：已有 key 但无计数意味着可能已经发过 POST，保守按 1 次。
    : 1
  return writePending({
    ...entry,
    startAttemptCount: previousAttempts + 1,
    lastStartAttemptAt: Date.now(),
  })
}

export function markPendingDeviceEntryStarted(
  entryId: string,
  accepted: DeliverySessionAccepted,
): PendingDeviceEntry | undefined {
  const entry = peekPendingDeviceEntry()
  if (!entry || entry.entryId !== entryId) return undefined
  const started = writePending({ ...entry, accepted })
  if (!started) return undefined
  rememberHandled(started)
  return started
}

/** 只供用户主动退出登录使用，不把该二维码标为已处理。 */
export function clearPendingDeviceEntry(): void {
  removePending()
}

export function dismissPendingDeviceEntry(entryId?: string): boolean {
  const entry = peekPendingDeviceEntry()
  if (!entry || (entryId && entry.entryId !== entryId)) return false
  rememberHandled(entry)
  removePending()
  return true
}

/** 取消手机号等前置动作时，只允许丢弃尚未发出过 POST 的新鲜入口。 */
export function dismissUnstartedPendingDeviceEntry(
  entryId?: string,
): boolean {
  const entry = peekPendingDeviceEntry()
  if (
    !entry
    || (entryId && entry.entryId !== entryId)
    || entry.idempotencyKey
    || entry.accepted
  ) {
    return false
  }
  return dismissPendingDeviceEntry(entry.entryId)
}

export function completePendingDeviceEntry(entryId: string): boolean {
  return dismissPendingDeviceEntry(entryId)
}

export function routePendingDeviceEntry(
  session: LoginResponse | undefined,
): boolean {
  const entry = peekPendingDeviceEntry()
  if (!entry) return false
  // 会话缺失时保留扫码意图，登录页会携带设备归因并在登录后继续。
  if (!session) return false
  if (
    session.audience !== 'miniapp'
    || session.entryMode !== 'USER'
  ) {
    dismissPendingDeviceEntry(entry.entryId)
    wx.showToast({
      title: '当前账号不能进行用户投递',
      icon: 'none',
    })
    return false
  }
  if (Date.now() < routeBlockedUntil) return false
  if (routingPendingEntry) return true
  routingPendingEntry = true
  wx.reLaunch({
    url: '/pages/delivery-entry/delivery-entry',
    fail: () => {
      routeBlockedUntil = Date.now() + 3000
      wx.showToast({
        title: '暂时无法打开投递页面',
        icon: 'none',
      })
      wx.switchTab({ url: '/pages/home/home' })
    },
    complete: () => {
      routingPendingEntry = false
    },
  })
  return true
}
