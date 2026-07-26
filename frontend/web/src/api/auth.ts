import request, { ApiProblem, invalidateCsrfToken } from './request';
import type { LoginRequest, LoginResponse, WebLoginDomain } from '@/types';

const DOMAIN_KEY = 'ecobin.web.login-domain';

function sessionPath(domain: WebLoginDomain): string {
  return domain === 'platform'
    ? '/api/v1/web/platform/auth/sessions'
    : '/api/v1/web/auth/sessions';
}

function rememberDomain(domain: WebLoginDomain): void {
  sessionStorage.setItem(DOMAIN_KEY, domain);
}

function rememberedDomain(): WebLoginDomain | null {
  const value = sessionStorage.getItem(DOMAIN_KEY);
  return value === 'platform' || value === 'tenant' ? value : null;
}

export async function login(domain: WebLoginDomain, data: LoginRequest) {
  const session = await request<LoginResponse>({
    url: sessionPath(domain),
    method: 'POST',
    data,
    noStore: true,
    unauthorized: 'ignore',
  });
  rememberDomain(domain);
  invalidateCsrfToken();
  return session;
}

export async function getCurrentSession(
  domain: WebLoginDomain,
): Promise<LoginResponse> {
  return request<LoginResponse>({
    url: `${sessionPath(domain)}/current`,
    method: 'GET',
    noStore: true,
    silent: true,
    unauthorized: 'ignore',
  });
}

export async function bootstrapCurrentSession(): Promise<{
  session: LoginResponse;
  domain: WebLoginDomain;
}> {
  const remembered = rememberedDomain();
  const domains: WebLoginDomain[] = remembered
    ? [remembered]
    : ['tenant', 'platform'];
  let lastProblem: ApiProblem | null = null;
  for (const domain of domains) {
    try {
      return { session: await getCurrentSession(domain), domain };
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
    sessionStorage.removeItem(DOMAIN_KEY);
    invalidateCsrfToken();
  }
}
