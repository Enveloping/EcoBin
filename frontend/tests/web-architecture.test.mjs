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

test('platform administrator governance stays root-only while self-service stays universal', () => {
  const routeSource = readFileSync(
    new URL('src/router/routes.tsx', webRoot),
    'utf8',
  );
  const apiSource = readFileSync(
    new URL('src/api/platformAdminAccounts.ts', webRoot),
    'utf8',
  );
  const pageSource = readFileSync(
    new URL('src/pages/platform-admins/index.tsx', webRoot),
    'utf8',
  );
  const accountSource = readFileSync(
    new URL('src/pages/account/index.tsx', webRoot),
    'utf8',
  );

  assert.match(
    routeSource,
    /path: '\/platform-admins'[\s\S]*?allOf: \['platform-account\.manage'\]/,
  );
  assert.match(routeSource, /ALL_WEB_ACCOUNTS/);
  assert.match(apiSource, /intent\.execute/);
  assert.doesNotMatch(apiSource, /randomUUID|Math\.random/);
  assert.match(pageSource, /administrator\.adminKind === 'DEFAULT'/);
  assert.match(pageSource, /永久逻辑删除/);
  assert.match(accountSource, /session\.accountType === 'PLATFORM_ADMIN'/);
  assert.match(accountSource, /changeCurrentPlatformAdministratorPassword/);
});

test('factory operators are independent identities with a hidden miniapp entry', () => {
  const routeSource = readFileSync(
    new URL('src/router/routes.tsx', webRoot),
    'utf8',
  );
  const operatorApi = readFileSync(
    new URL('src/api/factoryOperators.ts', webRoot),
    'utf8',
  );
  const operatorPage = readFileSync(
    new URL('src/pages/factory-operators/index.tsx', webRoot),
    'utf8',
  );
  const maintenancePanel = readFileSync(
    new URL('src/pages/account/MaintenanceAccessPanel.tsx', webRoot),
    'utf8',
  );
  const miniRoot = new URL('../miniprogram/miniprogram/', import.meta.url);
  const appJson = readFileSync(new URL('app.json', miniRoot), 'utf8');
  const appSource = readFileSync(new URL('app.ts', miniRoot), 'utf8');
  const profileSource = readFileSync(
    new URL('pages/profile/profile.ts', miniRoot),
    'utf8',
  );
  const bindingPage = readFileSync(
    new URL('factory/pages/bind/bind.ts', miniRoot),
    'utf8',
  );

  assert.match(
    routeSource,
    /path: '\/factory-operators'[\s\S]*?allOf: \['platform-admin\.manage'\]/,
  );
  assert.match(operatorApi, /\/api\/v1\/web\/platform\/factory-operators/);
  assert.match(operatorApi, /intent\.execute/);
  assert.match(operatorPage, /miniProgramCodeDataUrl/);
  assert.match(operatorPage, /新建厂家操作员/);
  assert.doesNotMatch(maintenancePanel, /miniapp|绑定二维码|工厂验收/);

  assert.match(appJson, /"root":\s*"factory"/);
  assert.match(appJson, /"pages\/bind\/bind"/);
  assert.doesNotMatch(appJson, /pages\/factory-acceptance/);
  assert.match(appSource, /enterFactoryIfBound/);
  assert.match(appSource, /await enterFactoryIfBound\(sequence\)/);
  assert.match(
    appSource,
    /if \(!isFactoryBindingKnown\(\)\) return false[\s\S]*?routeToFactoryAcceptance\(deviceCode\)/,
  );
  assert.doesNotMatch(profileSource, /工厂验收|factory\/pages/);
  assert.match(bindingPage, /consumeFactoryBindingToken/);
  assert.match(bindingPage, /loginFactory\(token\)/);
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

test('cleaning Web slice separates operation facts from editable records', () => {
  const routeSource = readFileSync(
    new URL('src/router/routes.tsx', webRoot),
    'utf8',
  );
  const operationApi = readFileSync(
    new URL('src/api/cleanOperations.ts', webRoot),
    'utf8',
  );
  const recordApi = readFileSync(
    new URL('src/api/cleanRecords.ts', webRoot),
    'utf8',
  );
  const operationPage = readFileSync(
    new URL('src/pages/clean-operations/index.tsx', webRoot),
    'utf8',
  );
  const recordPage = readFileSync(
    new URL('src/pages/clean-records/index.tsx', webRoot),
    'utf8',
  );
  const generatedApiTypes = readFileSync(
    new URL('src/api/generated/openapi.d.ts', webRoot),
    'utf8',
  );

  assert.match(routeSource, /path: '\/clean-operations'[\s\S]*?clean\.read/);
  assert.match(routeSource, /path: '\/clean-records'[\s\S]*?clean\.read/);
  assert.match(routeSource, /name: '清运管理'/);
  assert.doesNotMatch(routeSource, /无效清运订单/);
  assert.match(operationApi, /Schemas\['WebCleanOperationItem'\]/);
  assert.match(operationApi, /listOrganizationCleanOperations/);
  assert.match(recordApi, /Schemas\['WebCleanRecordDetail'\]/);
  assert.match(recordApi, /intent\.execute/);
  assert.match(operationPage, /RECOVERY_REQUIRED/);
  assert.match(operationPage, /PRE_UNLOCK_ENDED/);
  assert.match(operationPage, /ABORTED/);
  assert.match(recordPage, /expectedVersion:\s*detail\.effective\.version/);
  assert.match(recordPage, /listCleanRecordChanges/);
  assert.match(recordPage, /清运记录修正失败/);
  assert.match(recordPage, /修正历史加载失败/);
  assert.match(
    generatedApiTypes,
    /postCleanDetection: components\["schemas"\]\["CleanDetectionSummary"\] \| null;/,
  );
  assert.match(recordPage, /detail\.postCleanDetection\?\.status/);
  assert.doesNotMatch(
    recordPage,
    /detail\.postCleanDetection\.(?:status|finalResult|failureCode|completedAt)/,
  );
  assert.doesNotMatch(recordPage, /审核|拒绝/);
});

test('business rules stay versioned under one configuration menu', () => {
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
  const fundsSource = readFileSync(
    new URL('src/pages/funds/index.tsx', webRoot),
    'utf8',
  );
  const withdrawalConfigurationSource = readFileSync(
    new URL('src/pages/withdrawal-configuration/index.tsx', webRoot),
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
    /path: '\/configurations\/delivery'[\s\S]*?delivery\.configuration\.manage/,
  );
  assert.match(
    routeSource,
    /path: '\/configurations\/withdrawal'[\s\S]*?withdrawal\.configuration\.manage/,
  );
  assert.match(routeSource, /name: '配置管理'/);
  assert.match(routeSource, /name: '投递审核规则'/);
  assert.match(routeSource, /name: '提现审核规则'/);
  assert.doesNotMatch(routeSource, /投递与审核规则|name: '提现规则'/);
  assert.match(
    routeSource,
    /LegacyDeliveryConfigurationRedirect[\s\S]*?location\.search/,
  );
  assert.match(apiSource, /DeliveryConfigurationReleaseRequest/);
  assert.match(apiSource, /intent\.execute/);
  assert.match(pageSource, /OrganizationDeliveryConfiguration/);
  assert.doesNotMatch(
    organizationSource,
    /delivery\.configuration\.manage|OrganizationDeliveryConfiguration/,
  );
  assert.doesNotMatch(
    fundsSource,
    /getWithdrawalConfiguration|releaseWithdrawalConfiguration|提现(?:审核)?规则/,
  );
  assert.match(configurationSource, /expectedLatestVersion:\s*current\.versionNo/);
  assert.match(configurationSource, /automaticReviewMaxAmountYuan/);
  assert.match(configurationSource, /label="投递订单审核方式"/);
  assert.doesNotMatch(configurationSource, /label="审核方式"/);
  assert.match(configurationSource, /onFinish=\{\(values\) => void submit\(values\)\}/);
  assert.match(configurationSource, /保存投递配置/);
  assert.match(configurationSource, /系统会自动保留修改记录/);
  assert.doesNotMatch(
    configurationSource,
    /发布新版本|确认发布|<Modal/,
  );
  assert.match(configurationSource, /requestSequence/);
  assert.doesNotMatch(configurationSource, /randomUUID|Math\.random/);
  assert.match(
    withdrawalConfigurationSource,
    /label="单次最大提现金额（元）"/,
  );
  assert.match(
    withdrawalConfigurationSource,
    /共同金额限制[\s\S]*手动提现审核[\s\S]*投递返现自动提现审核/,
  );
  assert.match(
    withdrawalConfigurationSource,
    /提现审核与投递审核相互独立/,
  );
  assert.match(
    withdrawalConfigurationSource,
    /手动提现自动批准金额上限（元）/,
  );
  assert.match(
    withdrawalConfigurationSource,
    /自动提现自动批准金额上限（元）/,
  );
  assert.match(
    withdrawalConfigurationSource,
    /onFinish=\{\(values\) => void save\(values\)\}/,
  );
  assert.match(withdrawalConfigurationSource, /保存提现配置/);
  assert.doesNotMatch(
    withdrawalConfigurationSource,
    /发布新版本|确认发布|<Modal/,
  );
  assert.match(
    withdrawalConfigurationSource,
    /directory\.context\?\.domain !== 'platform'/,
  );
  assert.match(withdrawalConfigurationSource, /releaseWithdrawalConfiguration/);
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
  const configurationModalSource = readFileSync(
    new URL(
      'src/pages/device-management/DeviceConfigurationModal.tsx',
      webRoot,
    ),
    'utf8',
  );
  const runtimePolicySource = readFileSync(
    new URL(
      'src/pages/device-management/RuntimeSnapshotPolicyModal.tsx',
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
  assert.match(apiSource, /getPlatformDeviceRuntime/);
  assert.match(apiSource, /getTenantDeviceRuntime/);
  assert.match(apiSource, /getOrganizationDeviceRuntime/);
  assert.match(apiSource, /reevaluateDeviceAcceptance/);
  assert.match(apiSource, /listPlatformDeviceConfigurationVersions/);
  assert.match(apiSource, /rollForwardPlatformDeviceConfiguration/);
  assert.match(apiSource, /resynchronizePlatformDeviceConfiguration/);
  assert.match(apiSource, /getPlatformRuntimeSnapshotPolicy/);
  assert.match(apiSource, /releasePlatformRuntimeSnapshotPolicy/);
  assert.match(apiSource, /\/organizations\/\$\{encodeURIComponent[\s\S]*?\/devices/);
  assert.match(apiSource, /intent\.executeAccepted/);
  assert.doesNotMatch(apiSource, /randomUUID|Math\.random/);
  assert.match(pageSource, /永久设备资产/);
  assert.match(pageSource, /永久分配租户/);
  assert.match(pageSource, /永久分配机构/);
  assert.doesNotMatch(pageSource, /factoryBags/);
  assert.match(pageSource, /共享小程序的设备出厂端/);
  assert.match(pageSource, /机构无需手动启用设备/);
  assert.match(pageSource, /联网、配置、安全、占用和软件状态/);
  assert.match(pageSource, /设备状态上报策略/);
  assert.match(pageSource, /联网状态/);
  assert.match(pageSource, /window\.setInterval\(refreshVisibleList, 15_000\)/);
  assert.match(runtimePolicySource, /不用于判断设备是否在线/);
  assert.match(runtimePolicySource, /fallbackIntervalMinutes/);
  assert.doesNotMatch(
    configurationModalSource,
    /name=\{\['device', 'edgeHeartbeat(?:IntervalMs|MissThreshold)'\]\}/,
  );
  assert.match(drawerSource, /mcuSimulated/);
  assert.match(drawerSource, /camerasSimulated/);
  assert.match(drawerSource, /本次验收尚未保存设备检查记录/);
  assert.match(drawerSource, /当前联网与最近运行状态/);
  assert.match(drawerSource, /每 15 秒自动刷新/);
  assert.match(drawerSource, /设备检查历史记录/);
  assert.match(drawerSource, /不代表设备当前状态/);
  assert.match(drawerSource, /activeKey=\{evidenceExpanded/);
  assert.match(drawerSource, /模拟来源/);
  assert.doesNotMatch(drawerSource, /模拟器（不能通过）/);
  assert.match(drawerSource, /expectedLatestVersion:\s*current\.versionNo/);
  assert.match(drawerSource, /系统会自动下发配置并测量厂家初始袋皮重/);
  assert.match(drawerSource, /配置下发与恢复/);
  assert.match(drawerSource, /重新下发当前版本/);
  assert.match(drawerSource, /发布修复版本/);
  assert.match(drawerSource, /同一版本的配置内容与记录不一致/);
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
