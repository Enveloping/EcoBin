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
import {
  LatestTargetRequestGuard,
  walletPreviewTargetKey,
} from '../web/src/utils/latestTargetRequest.ts';

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
    'src/pages/user/OrganizationUserBinding.tsx',
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
    /path: '\/user-bindings'[\s\S]*?<LegacyUserBindingsRedirect/,
  );
  assert.match(routeSource, /directoryPath\('\/organization-users'/);
  assert.doesNotMatch(
    routeSource,
    /for \(const path of \[[^\]]*'\/user-bindings'/,
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

test('delivery Web slice stays on generated contracts and additive commands', () => {
  const routeSource = readFileSync(
    new URL('src/router/routes.tsx', webRoot),
    'utf8',
  );
  const apiSource = readFileSync(
    new URL('src/api/deliveryOrders.ts', webRoot),
    'utf8',
  );
  const pageSource = readFileSync(
    new URL('src/pages/delivery-orders/index.tsx', webRoot),
    'utf8',
  );
  const modalSource = readFileSync(
    new URL('src/pages/delivery-orders/DeliveryReviewModal.tsx', webRoot),
    'utf8',
  );

  assert.match(
    routeSource,
    /path: '\/deliveries'[\s\S]*?<DeliveryOrdersPage \/>/,
  );
  assert.match(
    routeSource,
    /path: '\/clean-records'[\s\S]*?allOf: \['clean\.read'\]/,
  );
  assert.match(apiSource, /Schemas\['WebDeliveryOrderItem'\]/);
  assert.match(apiSource, /operations\['listWebDeliveryOrders'\]/);
  assert.match(apiSource, /intent\.execute/);
  assert.doesNotMatch(apiSource, /randomUUID|Math\.random/);
  assert.match(pageSource, /page\.nextCursor/);
  assert.match(pageSource, /detailRequestSequence/);
  assert.match(
    pageSource,
    /selectedDetailOrderNo\.current !== deliveryOrderNo/,
  );
  assert.match(
    pageSource,
    /selectedDetailOrderNo\.current !== detail\.deliveryOrderNo/,
  );
  assert.match(modalSource, /expectedRevisionNo/);
  assert.doesNotMatch(
    modalSource,
    /return onSubmit\(\{[\s\S]{0,400}finalAmountYuan/,
  );
});

test('organization delivery rules stay versioned and share one Web panel', () => {
  const routeSource = readFileSync(
    new URL('src/router/routes.tsx', webRoot),
    'utf8',
  );
  const apiSource = readFileSync(
    new URL('src/api/deliveryConfiguration.ts', webRoot),
    'utf8',
  );
  const pageSource = readFileSync(
    new URL('src/pages/delivery-configuration/index.tsx', webRoot),
    'utf8',
  );
  const organizationSource = readFileSync(
    new URL('src/pages/organization/index.tsx', webRoot),
    'utf8',
  );
  const configurationSource = readFileSync(
    new URL(
      'src/pages/organization/OrganizationDeliveryConfiguration.tsx',
      webRoot,
    ),
    'utf8',
  );
  assert.match(
    routeSource,
    /path: '\/delivery-configuration'[\s\S]*?delivery\.configuration\.manage/,
  );
  assert.match(apiSource, /DeliveryConfigurationReleaseRequest/);
  assert.match(apiSource, /intent\.execute/);
  assert.match(pageSource, /OrganizationDeliveryConfiguration/);
  assert.match(
    organizationSource,
    /hasCapability\('delivery\.configuration\.manage'\)/,
  );
  assert.match(organizationSource, /OrganizationDeliveryConfiguration/);
  assert.match(configurationSource, /expectedLatestVersion:\s*current\.versionNo/);
  assert.match(configurationSource, /requestSequence/);
  assert.doesNotMatch(configurationSource, /randomUUID|Math\.random/);
});

test('device Web slice keeps one permanent asset and automatic activation model', () => {
  const apiSource = readFileSync(
    new URL('src/api/deviceDirectory.ts', webRoot),
    'utf8',
  );
  const generatedSource = readFileSync(
    new URL('src/api/generated/openapi.d.ts', webRoot),
    'utf8',
  );
  const pageSource = readFileSync(
    new URL('src/pages/device-management/index.tsx', webRoot),
    'utf8',
  );
  const drawerSource = readFileSync(
    new URL(
      'src/pages/device-management/DeviceAssetDrawer.tsx',
      webRoot,
    ),
    'utf8',
  );
  const commandIntentSource = readFileSync(
    new URL('src/api/commandIntent.ts', webRoot),
    'utf8',
  );

  assert.match(apiSource, /Schemas\['DeviceAsset'\]/);
  assert.match(apiSource, /operations\['listPlatformDeviceAssets'\]/);
  assert.match(apiSource, /assignPlatformDeviceTenant/);
  assert.match(apiSource, /assignTenantDeviceOrganization/);
  assert.match(apiSource, /listDeviceAcceptanceEvidence/);
  assert.match(apiSource, /reevaluateDeviceAcceptance/);
  assert.match(apiSource, /\/organizations\/\$\{encodeURIComponent[\s\S]*?\/devices/);
  assert.match(apiSource, /intent\.executeAccepted/);
  assert.doesNotMatch(apiSource, /randomUUID|Math\.random/);
  assert.match(pageSource, /永久设备资产/);
  assert.match(pageSource, /永久分配租户/);
  assert.match(pageSource, /永久分配机构/);
  assert.match(pageSource, /factoryBags/);
  assert.match(pageSource, /联网即可使用/);
  assert.match(drawerSource, /mcuSimulated/);
  assert.match(drawerSource, /camerasSimulated/);
  assert.match(drawerSource, /真实设备联网后会自动提交验收证据/);
  assert.match(drawerSource, /expectedLatestVersion:\s*current\.versionNo/);
  assert.match(drawerSource, /系统会自动下发配置并测量厂家初始袋皮重/);
  assert.doesNotMatch(apiSource, /device-deployments|deploymentCode|TenantPool/);
  assert.doesNotMatch(pageSource, /部署进度|租户设备池|经营开关状态/);
  assert.doesNotMatch(drawerSource, /人工验收|现场验收|手动开启/);
  assert.doesNotMatch(generatedSource, /deploymentCode|device-deployments/);
  assert.match(commandIntentSource, /requestAccepted/);
});

test('wallet Web slice keeps independent access, generated types and opaque cursors', () => {
  const routeSource = readFileSync(
    new URL('src/router/routes.tsx', webRoot),
    'utf8',
  );
  const apiSource = readFileSync(
    new URL('src/api/wallet.ts', webRoot),
    'utf8',
  );
  const pageSource = readFileSync(
    new URL('src/pages/wallet-entries/index.tsx', webRoot),
    'utf8',
  );
  const drawerSource = readFileSync(
    new URL(
      'src/pages/wallet-entries/OrganizationUserWalletDrawer.tsx',
      webRoot,
    ),
    'utf8',
  );
  const organizationUserSource = readFileSync(
    new URL('src/pages/organization-user/index.tsx', webRoot),
    'utf8',
  );

  assert.match(
    routeSource,
    /path: '\/wallet-entries'[\s\S]*?allOf: \['wallet\.read'\]/,
  );
  assert.match(apiSource, /Schemas\['WalletSummary'\]/);
  assert.match(apiSource, /Schemas\['OrganizationWalletEntry'\]/);
  assert.match(
    apiSource,
    /operations\['listWebOrganizationWalletEntries'\]/,
  );
  assert.match(apiSource, /noStore:\s*true/);
  assert.doesNotMatch(apiSource, /randomUUID|Math\.random/);
  assert.match(pageSource, /page\.nextCursor/);
  assert.match(pageSource, /organizationUserUid/);
  assert.match(pageSource, /sourceNo/);
  assert.match(drawerSource, /requestSequence/);
  assert.match(drawerSource, /listOrganizationUserWalletEntries/);
  assert.match(organizationUserSource, /hasCapability\('wallet\.read'\)/);
  assert.match(organizationUserSource, /OrganizationUserWalletDrawer/);
});

test('organization miniapp configuration keeps secrets ephemeral and commands versioned', () => {
  const apiSource = readFileSync(
    new URL('src/api/identityDirectory.ts', webRoot),
    'utf8',
  );
  const organizationSource = readFileSync(
    new URL('src/pages/organization/index.tsx', webRoot),
    'utf8',
  );
  const miniappSource = readFileSync(
    new URL(
      'src/pages/organization/OrganizationMiniappConfiguration.tsx',
      webRoot,
    ),
    'utf8',
  );

  assert.match(apiSource, /Schemas\['MiniappConfiguration'\]/);
  assert.match(apiSource, /Schemas\['PutMiniappConfigurationRequest'\]/);
  assert.match(
    apiSource,
    /getOrganizationMiniappConfiguration[\s\S]*?noStore:\s*true/,
  );
  assert.match(apiSource, /miniapp-login\/\$\{enabled \? 'enablements' : 'disablements'\}/);
  assert.match(organizationSource, /hasCapability\('miniapp\.manage'\)/);
  assert.match(organizationSource, /OrganizationMiniappConfiguration/);
  assert.match(miniappSource, /appSecret:\s*''/);
  assert.match(miniappSource, /configuration\?\.version \?\? null/);
  assert.match(miniappSource, /error\.isVersionConflict/);
  assert.match(miniappSource, /requestSequence\.current !== sequence/);
  assert.doesNotMatch(
    miniappSource,
    /localStorage|sessionStorage|URLSearchParams|console\./,
  );
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
    directoryPath('/deliveries', {
      tenant: 'tenant-a',
      organization: 'org-a',
      deliveryOrderNo: 'DO-20260731-000001',
    }),
    '/deliveries?tenant=tenant-a&organization=org-a&deliveryOrderNo=DO-20260731-000001',
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

test('wallet preview accepts only the latest organization-user request', () => {
  const guard = new LatestTargetRequestGuard();
  const userA = walletPreviewTargetKey(
    'platform', 'tenant-a', 'organization-a', 'user-a',
  );
  const userB = walletPreviewTargetKey(
    'platform', 'tenant-a', 'organization-a', 'user-b',
  );
  const requestA = guard.begin(userA);
  const requestB = guard.begin(userB);

  assert.equal(requestA.signal.aborted, true);
  assert.equal(guard.accepts(requestA, userA), false);
  assert.equal(guard.accepts(requestB, userB), true);
  assert.equal(guard.accepts(requestB, userA), false);

  guard.invalidate();
  assert.equal(requestB.signal.aborted, true);
  assert.equal(guard.accepts(requestB, userB), false);

  const oldOrganization = guard.begin(walletPreviewTargetKey(
    'platform', 'tenant-a', 'organization-a', 'user-b',
  ));
  const newOrganizationKey = walletPreviewTargetKey(
    'platform', 'tenant-a', 'organization-b', 'user-b',
  );
  const newOrganization = guard.begin(newOrganizationKey);

  assert.equal(oldOrganization.signal.aborted, true);
  assert.equal(guard.accepts(oldOrganization, newOrganizationKey), false);
  assert.equal(guard.accepts(newOrganization, newOrganizationKey), true);
});
