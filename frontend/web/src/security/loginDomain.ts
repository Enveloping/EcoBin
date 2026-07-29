import type { LoginResponse, WebLoginDomain } from '@/types';

export const WEB_LOGIN_DOMAIN_KEY = 'ecobin.web.login-domain';
export const DEFAULT_WEB_LOGIN_DOMAIN: WebLoginDomain = 'tenant';

type LoginDomainStorage = Pick<Storage, 'getItem' | 'setItem'>;

export function parseWebLoginDomain(
  value: string | null | undefined,
): WebLoginDomain | null {
  return value === 'platform' || value === 'tenant' ? value : null;
}

function browserSessionStorage(): LoginDomainStorage | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function storedLoginDomain(
  storage: LoginDomainStorage | null = browserSessionStorage(),
): WebLoginDomain | null {
  if (!storage) return null;
  try {
    return parseWebLoginDomain(storage.getItem(WEB_LOGIN_DOMAIN_KEY));
  } catch {
    return null;
  }
}

export function preferredLoginDomain(
  storage: LoginDomainStorage | null = browserSessionStorage(),
): WebLoginDomain {
  return storedLoginDomain(storage) ?? DEFAULT_WEB_LOGIN_DOMAIN;
}

export function rememberLoginDomain(
  domain: WebLoginDomain,
  storage: LoginDomainStorage | null = browserSessionStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(WEB_LOGIN_DOMAIN_KEY, domain);
  } catch {
    // A storage preference must never block authentication.
  }
}

/**
 * Both audiences share one browser Cookie. A different tab can therefore
 * replace the current audience while this tab still remembers the old one.
 */
export function loginDomainCandidates(
  preferred: WebLoginDomain | null,
): WebLoginDomain[] {
  const first = preferred ?? DEFAULT_WEB_LOGIN_DOMAIN;
  return first === 'tenant'
    ? ['tenant', 'platform']
    : ['platform', 'tenant'];
}

export function sessionBelongsToDomain(
  session: Pick<LoginResponse, 'accountType'>,
  domain: WebLoginDomain,
): boolean {
  return domain === 'platform'
    ? session.accountType === 'PLATFORM_ADMIN'
    : session.accountType === 'TENANT_PRINCIPAL'
      || session.accountType === 'STAFF';
}
