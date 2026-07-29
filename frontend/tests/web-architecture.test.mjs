import assert from 'node:assert/strict';
import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
} from 'node:fs';
import { extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { hasRouteAccess } from '../web/src/router/access.ts';
import {
  directoryPath,
  menuTargetPath,
} from '../web/src/router/directoryQuery.ts';
import {
  loginDomainCandidates,
  preferredLoginDomain,
  rememberLoginDomain,
  sessionBelongsToDomain,
  storedLoginDomain,
  WEB_LOGIN_DOMAIN_KEY,
} from '../web/src/security/loginDomain.ts';

const webRoot = new URL('../web/', import.meta.url);
const sourceRoot = fileURLToPath(new URL('../web/src/', import.meta.url));

function sourceFiles(directory) {
  return readdirSync(directory)
    .flatMap((name) => {
      const path = join(directory, name);
      if (statSync(path).isDirectory()) {
        if (name === 'generated') return [];
        return sourceFiles(path);
      }
      return ['.ts', '.tsx', '.css'].includes(extname(path)) ? [path] : [];
    });
}

function sourceText() {
  return sourceFiles(sourceRoot).map((path) => readFileSync(path, 'utf8')).join('\n');
}

test('legacy pages, APIs, numeric DTOs and decorative effects stay removed', () => {
  const deleted = [
    'src/pages/admin/index.tsx',
    'src/pages/dashboard/index.tsx',
    'src/pages/clean/index.tsx',
    'src/pages/delivery/index.tsx',
    'src/pages/device/index.tsx',
    'src/pages/door/index.tsx',
    'src/pages/statistics/index.tsx',
    'src/pages/user/index.tsx',
    'src/pages/withdraw/index.tsx',
    'src/pages/access/index.tsx',
    'src/api/admin.ts',
    'src/api/clean.ts',
    'src/api/delivery.ts',
    'src/api/device.ts',
    'src/api/door.ts',
    'src/api/statistics.ts',
    'src/api/tenant.ts',
    'src/api/user.ts',
    'src/api/withdraw.ts',
  ];
  for (const relative of deleted) {
    assert.equal(existsSync(new URL(relative, webRoot)), false, relative);
  }

  const text = sourceText();
  assert.doesNotMatch(
    text,
    /\b(?:Admin|Tenant|User|Device|Door|DeliveryOrder|CleanOrder|WithdrawOrder)\s*\{[\s\S]*?\bid\s*:\s*number/,
  );
  assert.doesNotMatch(
    text,
    /CustomCursor|ParticleBackground|AuroraBackground|MouseSpotlight|CountUp|useTilt|linear-gradient|radial-gradient|cursor:\s*none/i,
  );
});

test('application source can only request same-origin /api/v1 endpoints', () => {
  for (const path of sourceFiles(sourceRoot)) {
    const text = readFileSync(path, 'utf8');
    const endpointLiterals = text.matchAll(/(['"`])(\/api\/[^'"`\s]*)\1/g);
    for (const match of endpointLiterals) {
      assert.match(match[2], /^\/api\/v1\//, `${path}: ${match[2]}`);
    }
  }

  const requestSource = readFileSync(
    new URL('src/api/request.ts', webRoot),
    'utf8',
  );
  assert.match(requestSource, /Only same-origin \/api\/v1\/\*\* requests/);
  assert.doesNotMatch(requestSource, /VITE_API_BASE/);
});

test('web authentication keeps the frozen Cookie, CSRF and privacy boundaries', () => {
  const authSource = readFileSync(
    new URL('src/api/auth.ts', webRoot),
    'utf8',
  );
  const requestSource = readFileSync(
    new URL('src/api/request.ts', webRoot),
    'utf8',
  );
  const accountSource = readFileSync(
    new URL('src/pages/account/index.tsx', webRoot),
    'utf8',
  );

  assert.match(authSource, /await refreshCsrfToken\(\)/);
  assert.match(requestSource, /withCredentials:\s*true/);
  assert.match(requestSource, /problem\.code === 'SECURITY\.CSRF_INVALID'/);
  assert.match(requestSource, /retry-after/);
  assert.doesNotMatch(
    sourceText(),
    /\/api\/system\/auth\/login|Authorization.{0,80}Bearer|Bearer.{0,80}Authorization/,
  );
  assert.doesNotMatch(accountSource, /contactPhone|updateOwnProfile/);
});

test('route capability composition and caller-owned command intents are fixed', () => {
  const routeSource = readFileSync(
    new URL('src/router/routes.tsx', webRoot),
    'utf8',
  );
  assert.match(routeSource, /allOf\?: string\[\]/);
  assert.match(routeSource, /anyOf\?: string\[\]/);
  assert.match(
    routeSource,
    /path: '\/user-bindings'[\s\S]*?allOf: \['user\.read', 'staff\.bind'\]/,
  );

  const directorySource = readFileSync(
    new URL('src/api/identityDirectory.ts', webRoot),
    'utf8',
  );
  assert.doesNotMatch(directorySource, /randomUUID/);
  assert.match(directorySource, /intent\.execute/);
  assert.match(directorySource, /resetTenantPrincipalPassword/);
  assert.match(directorySource, /listOrganizationUsers/);
});

test('allOf and anyOf capability semantics are evaluated independently', () => {
  const session = {
    accountType: 'STAFF',
    capabilities: ['user.read', 'review.tenant'],
  };
  assert.equal(
    hasRouteAccess(session, { allOf: ['user.read', 'staff.bind'] }),
    false,
  );
  assert.equal(
    hasRouteAccess(session, {
      allOf: ['user.read'],
      anyOf: ['review.organization', 'review.tenant'],
    }),
    true,
  );
  assert.equal(
    hasRouteAccess(session, {
      anyOf: ['review.organization', 'funds.read'],
    }),
    false,
  );
  assert.equal(
    hasRouteAccess(session, { accountTypes: ['PLATFORM_ADMIN'] }),
    false,
  );
});

test('Web login-domain recovery follows the shared Cookie audience', () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };

  assert.equal(preferredLoginDomain(storage), 'tenant');
  rememberLoginDomain('platform', storage);
  assert.equal(values.get(WEB_LOGIN_DOMAIN_KEY), 'platform');
  assert.equal(storedLoginDomain(storage), 'platform');
  assert.deepEqual(
    loginDomainCandidates(storedLoginDomain(storage)),
    ['platform', 'tenant'],
  );
  assert.deepEqual(loginDomainCandidates('tenant'), ['tenant', 'platform']);
  assert.equal(
    sessionBelongsToDomain({ accountType: 'PLATFORM_ADMIN' }, 'platform'),
    true,
  );
  assert.equal(
    sessionBelongsToDomain({ accountType: 'STAFF' }, 'platform'),
    false,
  );
  assert.equal(
    sessionBelongsToDomain({ accountType: 'TENANT_PRINCIPAL' }, 'tenant'),
    true,
  );
});

test('directory deep links preserve only explicit non-sensitive scope', () => {
  assert.equal(
    directoryPath('/organization-users', {
      tenant: 'tenant-a',
      organization: 'org-a',
    }),
    '/organization-users?tenant=tenant-a&organization=org-a',
  );
  assert.equal(
    directoryPath('/deliveries', {
      tenant: 'tenant-a',
      organization: 'org-a',
      organizationUserUid: 'user-public-uid',
    }),
    '/deliveries?tenant=tenant-a&organization=org-a&organizationUserUid=user-public-uid',
  );
  assert.equal(
    menuTargetPath(
      '/organization-users?view=disabled',
      '?tenant=tenant-a&organization=org-secret&phone=13800138000',
      true,
    ),
    '/organization-users?view=disabled&tenant=tenant-a',
  );
});

test('data tables expose persisted column settings with safe defaults', () => {
  const pageStyle = readFileSync(
    new URL('src/utils/pageStyle.tsx', webRoot),
    'utf8',
  );
  const staffPage = readFileSync(
    new URL('src/pages/staff/index.tsx', webRoot),
    'utf8',
  );

  assert.match(pageStyle, /setting:\s*true/);
  assert.match(pageStyle, /data-table-workbench/);
  assert.match(staffPage, /securityVersion:\s*\{\s*show:\s*false\s*\}/);
  assert.match(staffPage, /<StaffAccessPanel/);
});
