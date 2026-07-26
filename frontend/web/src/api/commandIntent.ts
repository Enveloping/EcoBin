import request, { type ApiRequestConfig } from './request';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class ClientIntentConflictError extends Error {
  constructor() {
    super('同一用户意图不能在重试时改变请求内容，请创建新的操作意图');
    this.name = 'ClientIntentConflictError';
  }
}

function uuidV4(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join('-');
}

function stableJson(value: unknown): string {
  if (value === undefined) return '"__undefined__"';
  if (value === null || typeof value !== 'object') {
    const encoded = JSON.stringify(value);
    return encoded === undefined ? '"__unsupported__"' : encoded;
  }
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableJson(object[key])}`)
    .join(',')}}`;
}

function fingerprint(config: ApiRequestConfig): string {
  return stableJson({
    method: (config.method || 'GET').toUpperCase(),
    url: config.url,
    params: config.params,
    data: config.data,
  });
}

export interface CommandIntent {
  readonly idempotencyKey: string;
  execute<T, D = unknown>(config: ApiRequestConfig<D>): Promise<T>;
}

/** Create once on the first click; reuse this object for every retry of that intent. */
export function createCommandIntent(
  idempotencyKey = uuidV4(),
): CommandIntent {
  if (!UUID_V4.test(idempotencyKey)) {
    throw new TypeError('idempotencyKey must be a UUIDv4');
  }
  let firstFingerprint: string | null = null;
  return {
    idempotencyKey,
    async execute<T, D = unknown>(config: ApiRequestConfig<D>): Promise<T> {
      const current = fingerprint(config);
      if (firstFingerprint !== null && firstFingerprint !== current) {
        throw new ClientIntentConflictError();
      }
      firstFingerprint = current;
      return request<T, D>({ ...config, idempotencyKey });
    },
  };
}

export function withExpectedVersion<T extends object>(
  data: T,
  expectedVersion: number,
): T & { expectedVersion: number } {
  if (!Number.isSafeInteger(expectedVersion) || expectedVersion < 0) {
    throw new TypeError('expectedVersion must be a non-negative safe integer');
  }
  return { ...data, expectedVersion };
}
