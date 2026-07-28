import { request, type RequestOptions } from './request'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export class ClientIntentConflictError extends Error {
  constructor() {
    super('同一用户意图不能在重试时改变请求内容，请创建新的操作意图')
    this.name = 'ClientIntentConflictError'
  }
}

function randomBytes(length: number): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    wx.getRandomValues({
      length,
      success: (result) => resolve(new Uint8Array(result.randomValues)),
      fail: reject,
    })
  })
}

async function uuidV4(): Promise<string> {
  const bytes = await randomBytes(16)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(
    bytes,
    (value) => value.toString(16).padStart(2, '0'),
  ).join('')
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join('-')
}

export function createIdempotencyKey(): Promise<string> {
  return uuidV4()
}

function stableJson(value: unknown): string {
  if (value === undefined) return '"__undefined__"'
  if (value === null || typeof value !== 'object') {
    const encoded = JSON.stringify(value)
    return encoded === undefined ? '"__unsupported__"' : encoded
  }
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  const object = value as Record<string, unknown>
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableJson(object[key])}`)
    .join(',')}}`
}

function fingerprint(options: RequestOptions): string {
  return stableJson({
    method: (options.method ?? 'GET').toUpperCase(),
    url: options.url,
    data: options.data,
  })
}

export interface CommandIntent {
  readonly idempotencyKey: string
  execute<T>(options: RequestOptions): Promise<T>
}

/** 首次点击时创建一次；同一意图的网络重试继续复用该对象。 */
export async function createCommandIntent(
  idempotencyKey?: string,
): Promise<CommandIntent> {
  const key = idempotencyKey ?? await uuidV4()
  if (!UUID_V4.test(key)) {
    throw new TypeError('idempotencyKey must be a UUIDv4')
  }
  let firstFingerprint: string | null = null
  return {
    idempotencyKey: key,
    async execute<T>(options: RequestOptions): Promise<T> {
      const current = fingerprint(options)
      if (firstFingerprint !== null && firstFingerprint !== current) {
        throw new ClientIntentConflictError()
      }
      firstFingerprint = current
      return request<T>({ ...options, idempotencyKey: key })
    },
  }
}

export function withExpectedVersion<T extends object>(
  data: T,
  expectedVersion: number,
): T & { expectedVersion: number } {
  if (!Number.isSafeInteger(expectedVersion) || expectedVersion < 0) {
    throw new TypeError('expectedVersion must be a non-negative safe integer')
  }
  return { ...data, expectedVersion }
}
