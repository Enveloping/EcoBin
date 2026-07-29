import request, {
  ApiProblem,
  invalidateCsrfToken,
  refreshCsrfToken,
} from './request';
import type { LoginRequest, LoginResponse, WebLoginDomain } from '@/types';
import {
  loginDomainCandidates,
  rememberLoginDomain,
  sessionBelongsToDomain,
  storedLoginDomain,
} from '@/security/loginDomain';

function sessionPath(domain: WebLoginDomain): string {
  return domain === 'platform'
    ? '/api/v1/web/platform/auth/sessions'
    : '/api/v1/web/auth/sessions';
}

function requireDomainSession(
  session: LoginResponse,
  domain: WebLoginDomain,
): LoginResponse {
  if (sessionBelongsToDomain(session, domain)) return session;
  throw new ApiProblem(0, {
    code: 'AUTH.SESSION_DOMAIN_MISMATCH',
    message: '服务端返回的账号类型与登录入口不一致',
    requestId: '',
    retryable: false,
    details: {},
  });
}

export async function login(domain: WebLoginDomain, data: LoginRequest) {
  const session = requireDomainSession(
    await request<LoginResponse>({
      url: sessionPath(domain),
      method: 'POST',
      data,
      noStore: true,
      unauthorized: 'ignore',
    }),
    domain,
  );
  rememberLoginDomain(domain);
  await refreshCsrfToken();
  return session;
}

export async function getCurrentSession(
  domain: WebLoginDomain,
): Promise<LoginResponse> {
  return requireDomainSession(
    await request<LoginResponse>({
      url: `${sessionPath(domain)}/current`,
      method: 'GET',
      noStore: true,
      silent: true,
      unauthorized: 'ignore',
    }),
    domain,
  );
}

export async function bootstrapCurrentSession(): Promise<{
  session: LoginResponse;
  domain: WebLoginDomain;
}> {
  const domains = loginDomainCandidates(storedLoginDomain());
  let lastProblem: ApiProblem | null = null;
  for (const domain of domains) {
    try {
      const session = await getCurrentSession(domain);
      rememberLoginDomain(domain);
      return { session, domain };
    } catch (error) {
      if (error instanceof ApiProblem && error.status === 401) {
        lastProblem = error;
        continue;
      }
      throw error;
    }
  }
  throw lastProblem ?? new Error('No active Web session');
}

export async function logout(domain: WebLoginDomain): Promise<void> {
  try {
    await request<void>({
      url: `${sessionPath(domain)}/current`,
      method: 'DELETE',
      noStore: true,
      unauthorized: 'ignore',
    });
  } finally {
    rememberLoginDomain(domain);
    invalidateCsrfToken();
  }
}
