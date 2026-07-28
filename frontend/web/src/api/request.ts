import axios, {
  AxiosError,
  AxiosHeaders,
  type AxiosRequestConfig,
  type AxiosResponse,
} from 'axios';
import { message } from 'antd';
import { authActions } from '@/stores/authStore';

export interface ApiEnvelope<T> {
  code: 'OK';
  data: T;
  requestId: string;
}

export interface ProblemDetail {
  code: string;
  message: string;
  requestId: string;
  retryable: boolean;
  details: Record<string, unknown>;
}

export interface RequestTrace {
  requestId?: string;
  correlationId?: string;
}

export type UnauthorizedBehavior = 'redirect' | 'ignore';

export interface ApiRequestConfig<D = unknown> extends AxiosRequestConfig<D> {
  idempotencyKey?: string;
  noStore?: boolean;
  silent?: boolean;
  csrf?: boolean;
  unauthorized?: UnauthorizedBehavior;
}

interface InternalRequestConfig<D = unknown> extends ApiRequestConfig<D> {
  csrfRetried?: boolean;
}

interface ApiExecution<T> {
  data: T;
  status: number;
  location?: string;
  trace: RequestTrace;
}

interface CsrfTokenResponse {
  token: string;
  headerName: 'X-CSRF-TOKEN';
}

export class ApiProblem extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;
  readonly retryable: boolean;
  readonly details: Record<string, unknown>;

  constructor(status: number, problem: ProblemDetail) {
    super(problem.message);
    this.name = 'ApiProblem';
    this.status = status;
    this.code = problem.code;
    this.requestId = problem.requestId;
    this.retryable = problem.retryable;
    this.details = problem.details;
  }

  get isIdempotencyConflict(): boolean {
    return this.code === 'COMMON.IDEMPOTENCY_KEY_CONFLICT';
  }

  get isVersionConflict(): boolean {
    return (
      this.status === 409
      && (this.code.includes('VERSION') || this.code.includes('REVISION'))
    );
  }
}

const instance = axios.create({
  // Web authentication is intentionally same-origin. Development uses Vite's
  // /api proxy; production serves the SPA and API behind one origin.
  baseURL: '',
  timeout: 15000,
  withCredentials: true,
});

let csrfToken: string | null = null;
let csrfBootstrap: Promise<string> | null = null;
let lastTrace: RequestTrace = {};
let redirecting = false;

export function normalizeApiUrl(url: string | undefined): string {
  if (!url) {
    throw new TypeError('API request URL is required');
  }
  if (
    /^([a-z][a-z\d+\-.]*:)?\/\//i.test(url)
    || !url.startsWith('/api/v1/')
  ) {
    throw new TypeError(
      `Only same-origin /api/v1/** requests are allowed: ${url}`,
    );
  }
  return url;
}

function isUnsafe(method: string | undefined): boolean {
  return !['GET', 'HEAD', 'OPTIONS'].includes((method || 'GET').toUpperCase());
}

function captureTrace(response: AxiosResponse<ApiEnvelope<unknown>>): RequestTrace {
  const requestId = response.data?.requestId || response.headers['x-request-id'];
  const correlationId = response.headers['x-correlation-id'];
  lastTrace = {
    requestId: typeof requestId === 'string' ? requestId : undefined,
    correlationId: typeof correlationId === 'string' ? correlationId : undefined,
  };
  return lastTrace;
}

function isProblemDetail(value: unknown): value is ProblemDetail {
  if (!value || typeof value !== 'object') return false;
  const problem = value as Partial<ProblemDetail>;
  return (
    typeof problem.code === 'string'
    && typeof problem.message === 'string'
    && typeof problem.requestId === 'string'
    && typeof problem.retryable === 'boolean'
    && !!problem.details
    && typeof problem.details === 'object'
  );
}

function networkProblem(error: AxiosError): ApiProblem {
  const status = error.response?.status || 0;
  const hasResponse = !!error.response;
  return new ApiProblem(status, {
    code: hasResponse ? 'COMMON.INVALID_RESPONSE' : 'COMMON.NETWORK_ERROR',
    message: hasResponse
      ? `服务端返回了无法识别的错误响应(${status})`
      : error.message || '网络异常，请稍后重试',
    requestId: '',
    retryable: !hasResponse || status >= 500,
    details: {},
  });
}

function parseProblem(error: unknown): ApiProblem {
  if (error instanceof ApiProblem) return error;
  if (!axios.isAxiosError(error)) {
    return new ApiProblem(0, {
      code: 'COMMON.CLIENT_ERROR',
      message: error instanceof Error ? error.message : '客户端请求失败',
      requestId: '',
      retryable: false,
      details: {},
    });
  }
  if (isProblemDetail(error.response?.data)) {
    return new ApiProblem(error.response?.status || 0, error.response.data);
  }
  return networkProblem(error);
}

async function getCsrfToken(): Promise<string> {
  if (csrfToken) return csrfToken;
  if (!csrfBootstrap) {
    csrfBootstrap = instance
      .get<ApiEnvelope<CsrfTokenResponse>>('/api/v1/web/auth/csrf-token', {
        headers: {
          'Cache-Control': 'no-store',
          Pragma: 'no-cache',
        },
      })
      .then((response) => {
        captureTrace(response);
        if (response.data?.code !== 'OK') {
          throw new Error('CSRF bootstrap returned an invalid response envelope');
        }
        if (response.data.data.headerName !== 'X-CSRF-TOKEN') {
          throw new Error('CSRF bootstrap returned an unexpected header name');
        }
        csrfToken = response.data.data.token;
        return csrfToken;
      })
      .finally(() => {
        csrfBootstrap = null;
      });
  }
  return csrfBootstrap;
}

export function invalidateCsrfToken(): void {
  csrfToken = null;
  csrfBootstrap = null;
}

export function getLastRequestTrace(): Readonly<RequestTrace> {
  return lastTrace;
}

function handleUnauthorized(): void {
  authActions.clear();
  invalidateCsrfToken();
  if (!redirecting && location.pathname !== '/login') {
    redirecting = true;
    location.assign('/login');
  }
}

async function execute<T, D = unknown>(
  original: InternalRequestConfig<D>,
): Promise<ApiExecution<T>> {
  const config: InternalRequestConfig<D> = {
    ...original,
    url: normalizeApiUrl(original.url),
  };
  const headers = AxiosHeaders.from(
    original.headers as AxiosHeaders | undefined,
  );
  if (config.noStore) {
    headers.set('Cache-Control', 'no-store');
    headers.set('Pragma', 'no-cache');
  }
  if (config.idempotencyKey) {
    headers.set('Idempotency-Key', config.idempotencyKey);
  }
  const needsCsrf = config.csrf ?? isUnsafe(config.method);
  if (needsCsrf) {
    try {
      headers.set('X-CSRF-TOKEN', await getCsrfToken());
    } catch (error) {
      const problem = parseProblem(error);
      if (problem.status === 401 && original.unauthorized !== 'ignore') {
        handleUnauthorized();
      }
      if (
        !original.silent
        && (problem.status !== 401 || original.unauthorized === 'ignore')
      ) {
        message.error(problem.message);
      }
      throw problem;
    }
  }
  config.headers = headers;

  delete config.idempotencyKey;
  delete config.noStore;
  delete config.silent;
  delete config.csrf;
  delete config.unauthorized;
  delete config.csrfRetried;

  try {
    const response = await instance.request<ApiEnvelope<T>>(config);
    const trace = captureTrace(response);
    if (needsCsrf) {
      // The target security chain may rotate the readable CSRF cookie after
      // an authenticated unsafe request. Never reuse the pre-request header.
      invalidateCsrfToken();
    }
    if (response.status === 204) {
      return {
        data: undefined as T,
        status: response.status,
        trace,
      };
    }
    if (!response.data || response.data.code !== 'OK') {
      throw new Error('HTTP success response does not match the EcoBin envelope');
    }
    const locationHeader = response.headers.location;
    return {
      data: response.data.data,
      status: response.status,
      location: typeof locationHeader === 'string' ? locationHeader : undefined,
      trace,
    };
  } catch (error) {
    const problem = parseProblem(error);
    if (
      problem.code === 'SECURITY.CSRF_INVALID'
      && needsCsrf
      && !original.csrfRetried
    ) {
      invalidateCsrfToken();
      return execute<T, D>({ ...original, csrfRetried: true });
    }
    if (problem.status === 401 && original.unauthorized !== 'ignore') {
      handleUnauthorized();
    }
    if (
      !original.silent
      && (problem.status !== 401 || original.unauthorized === 'ignore')
    ) {
      message.error(problem.message);
    }
    throw problem;
  }
}

/** Resolve with the successful envelope data. HTTP status remains authoritative. */
export async function request<T, D = unknown>(
  config: ApiRequestConfig<D>,
): Promise<T> {
  return (await execute<T, D>(config)).data;
}

/** Require a real 202 and verify Location agrees with the body statusUrl. */
export async function requestAccepted<
  T extends { statusUrl: string },
  D = unknown,
>(config: ApiRequestConfig<D>): Promise<T> {
  const result = await execute<T, D>(config);
  if (result.status !== 202) {
    throw new Error(`Expected HTTP 202, received ${result.status}`);
  }
  if (result.location && result.location !== result.data.statusUrl) {
    throw new Error('HTTP Location differs from the accepted operation statusUrl');
  }
  return result.data;
}

export default request;
