import request from './request';

export interface AcceptedOperation {
  operationId: string;
  resourceId: string;
  status: 'ACCEPTED' | 'PROCESSING' | 'UNKNOWN';
  statusUrl: string;
  recommendedPollAfterMs: number;
}

export interface PollableProjection {
  status: string;
  phase?: string;
  recommendedPollAfterMs?: number;
}

export interface PollOptions<T extends PollableProjection> {
  operation: AcceptedOperation;
  isTerminal: (projection: T) => boolean;
  onUpdate?: (projection: T) => void;
  signal?: AbortSignal;
  maximumElapsedMs?: number;
}

const STORAGE_PREFIX = 'ecobin.pending-operation.';
const STATUS_URL = /^\/api\/v1\/(web|miniapp|miniapp-staff)\//;

function validateStatusUrl(statusUrl: string): void {
  if (!STATUS_URL.test(statusUrl)) {
    throw new TypeError('statusUrl must be a same-origin EcoBin /api/v1 path');
  }
}

function delay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const timer = setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}

function pollDelay(value: number | undefined): number {
  if (!Number.isFinite(value)) return 1500;
  return Math.min(60000, Math.max(250, Math.trunc(value as number)));
}

const UNCHANGED_BACKOFF_MS = [1000, 2000, 3000, 5000, 10000];

function projectionKey(projection: PollableProjection): string {
  return `${projection.status}|${projection.phase || ''}`;
}

export function rememberAcceptedOperation(operation: AcceptedOperation): void {
  validateStatusUrl(operation.statusUrl);
  sessionStorage.setItem(
    `${STORAGE_PREFIX}${operation.resourceId}`,
    JSON.stringify(operation),
  );
}

export function restoreAcceptedOperation(
  resourceId: string,
): AcceptedOperation | null {
  const raw = sessionStorage.getItem(`${STORAGE_PREFIX}${resourceId}`);
  if (!raw) return null;
  try {
    const operation = JSON.parse(raw) as AcceptedOperation;
    if (operation.resourceId !== resourceId) throw new Error('resource mismatch');
    validateStatusUrl(operation.statusUrl);
    return operation;
  } catch {
    sessionStorage.removeItem(`${STORAGE_PREFIX}${resourceId}`);
    return null;
  }
}

export function forgetAcceptedOperation(resourceId: string): void {
  sessionStorage.removeItem(`${STORAGE_PREFIX}${resourceId}`);
}

export async function pollAcceptedOperation<T extends PollableProjection>(
  options: PollOptions<T>,
): Promise<T> {
  const { operation, isTerminal, onUpdate, signal } = options;
  validateStatusUrl(operation.statusUrl);
  rememberAcceptedOperation(operation);
  const startedAt = Date.now();
  const maximumElapsedMs = options.maximumElapsedMs ?? 5 * 60 * 1000;
  let previousKey: string | undefined;
  let unchangedCount = 0;

  while (true) {
    const projection = await request<T>({
      url: operation.statusUrl,
      method: 'GET',
      noStore: true,
      silent: true,
    });
    onUpdate?.(projection);
    if (isTerminal(projection)) {
      forgetAcceptedOperation(operation.resourceId);
      return projection;
    }
    if (Date.now() - startedAt >= maximumElapsedMs) {
      throw new Error('异步状态仍未结束，可稍后按资源编号继续查询');
    }
    const key = projectionKey(projection);
    if (key === previousKey) {
      unchangedCount = Math.min(
        unchangedCount + 1,
        UNCHANGED_BACKOFF_MS.length - 1,
      );
    } else {
      unchangedCount = 0;
      previousKey = key;
    }
    const waitMs = Math.max(
      pollDelay(projection.recommendedPollAfterMs),
      UNCHANGED_BACKOFF_MS[unchangedCount],
    );
    await delay(waitMs, signal);
  }
}
