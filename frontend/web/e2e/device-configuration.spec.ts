import { expect, test, type Page, type Route } from '@playwright/test';
import { permanentDeviceAsset, permanentDeviceRuntime } from './fixtures/device';

const policyPath = '/api/v1/web/platform/device-configuration-policy';
const now = '2026-09-12T08:00:00.000Z';
const defaultPolicy = {
  version: 1, defaultVersion: 1, configurationMode: 'DEFAULT', unitPriceYuanPerKg: '0.4500', negativeWeightThresholdGram: 500,
  platformDefaults: { unitPriceYuanPerKg: '0.4500', fullnessMode: 'INFRARED_OR_WEIGHT', fullnessWeightKg: '50.000', negativeWeightThresholdGram: 500 },
  fullnessMode: 'INFRARED_OR_WEIGHT', fullnessWeightKg: '50.000',
  publicationSource: 'SYSTEM', updatedBy: '系统', changeReason: '默认规则', updatedAt: now,
  rolloutUid: '11111111-1111-4111-8111-111111111170', rolloutStatus: 'DONE',
  targetDeviceCount: 3, processedDeviceCount: 3, publishedDeviceCount: 3,
  pendingDeviceCount: 2, edgeSavedDeviceCount: 0, appliedDeviceCount: 1, failedDeviceCount: 0, blockedDeviceCount: 0,
};

async function ok(route: Route, data: unknown) {
  await route.fulfill({ json: { code: 'OK', data, requestId: 'fullness-test' } });
}
async function reject(route: Route, status = 401) {
  await route.fulfill({ status, json: { code: 'SECURITY.UNAUTHENTICATED', message: '未登录', requestId: 'fullness-test' } });
}

async function platformSetup(page: Page, accountType = 'PLATFORM_ADMIN', tenantGrant = false) {
  const platform = accountType === 'PLATFORM_ADMIN';
  const scopePath = platform ? policyPath : '/api/v1/web/device-configuration-policy';
  const state = { policy: { ...defaultPolicy, configurationMode: platform ? 'DEFAULT' : 'INHERIT', version: platform ? 1 : 0 }, writes: [] as Array<Record<string, unknown>> };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/csrf-token')) return ok(route, { token: 'test-csrf', headerName: 'X-CSRF-TOKEN' });
    if (path === '/api/v1/web/auth/sessions/current' || path === '/api/v1/web/platform/auth/sessions/current') {
      if (path.includes('/platform/') !== platform) return reject(route);
      return ok(route, {
        sessionUid: '11111111-1111-4111-8111-111111111171', subjectUid: '11111111-1111-4111-8111-111111111172',
        accountType, displayName: '测试操作者', capabilities: ['device.read', platform ? 'device.manage' : 'device.configuration.manage'],
        tenantCapabilities: tenantGrant ? ['device.configuration.manage'] : [],
        organizations: [], tenantCode: platform ? null : 'tenant-a', version: 1, authVersion: 1, expiresAt: '2030-01-01T00:00:00Z',
      });
    }
    if (path === scopePath) return ok(route, state.policy);
    if (path === `${scopePath}/releases`) {
      const body = route.request().postDataJSON();
      state.writes.push(body);
      expect(route.request().headers()['idempotency-key']).toBeTruthy();
      state.policy = { ...state.policy, ...body, ...(body.configurationMode === 'INHERIT' ? state.policy.platformDefaults : {}), version: state.policy.version + 1, rolloutStatus: 'PENDING', appliedDeviceCount: 0, pendingDeviceCount: 3 };
      return route.fulfill({ status: 202, json: { code: 'OK', data: state.policy, requestId: 'test-release' } });
    }
    return reject(route, 404);
  });
  return state;
}

test('global fullness stays compact and publishes all-device rules only on explicit submission', async ({ page }) => {
  const state = await platformSetup(page);
  await page.goto('/configurations/device');
  await expect(page.getByText('平台默认配置', { exact: false })).toBeVisible();
  await expect(page.getByText('50.000 千克', { exact: true })).toBeVisible();
  await page.getByRole('menuitem', { name: /配置管理$/ }).click();
  await expect(page.getByRole('menuitem', { name: '设备配置', exact: true })).toBeVisible();
  await page.getByRole('menuitem', { name: '设备配置', exact: true }).click();
  await expect(page.getByText('配置已生成', { exact: true })).toBeVisible();
  await expect(page.getByText('全部设备已应用', { exact: true })).not.toBeVisible();
  await expect(page.getByText(/适用于使用平台默认值的租户/)).not.toBeVisible();
  await page.getByRole('button', { name: '设备配置适用范围说明', exact: true }).focus();
  await expect(page.getByText(/适用于使用平台默认值的租户/)).toBeVisible();
  await page.getByRole('button', { name: '修改设置', exact: true }).click();
  const modal = page.getByRole('dialog', { name: '修改设备配置' });
  await modal.getByLabel('仅红外', { exact: true }).check();
  await expect(modal.getByLabel('满溢净重（千克）')).not.toBeVisible();
  expect(state.writes).toHaveLength(0);
  await modal.getByLabel('修改原因').fill('统一使用红外');
  await modal.getByRole('button', { name: '发布默认配置' }).click();
  await expect(modal).not.toBeVisible();
  expect(state.writes[0]).toMatchObject({ expectedVersion: 1, expectedDefaultVersion: 1, fullnessMode: 'INFRARED_ONLY', fullnessWeightKg: '50.000' });
  await expect(page.getByText('正在生成配置', { exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('fullness-default.png'), animations: 'disabled' });
});

test('editing keeps the reviewed version and allows a three-decimal weight', async ({ page }) => {
  const state = await platformSetup(page);
  await page.goto('/configurations/device');
  await page.getByRole('button', { name: '修改设置', exact: true }).click();
  const modal = page.getByRole('dialog', { name: '修改设备配置' });
  state.policy = { ...state.policy, version: 2, fullnessWeightKg: '90.000' };
  // A background refresh must not silently change the operator's expectedVersion.
  await expect(page.getByText('90.000 千克', { exact: true })).toBeVisible({ timeout: 8000 });
  await modal.getByLabel('仅重量', { exact: true }).check();
  await modal.getByLabel('满溢净重（千克）').fill('65.125');
  await modal.getByLabel('修改原因').fill('统一重量阈值');
  await page.screenshot({ path: test.info().outputPath('fullness-edit.png'), animations: 'disabled' });
  await modal.getByRole('button', { name: '发布默认配置' }).click();
  await expect(modal).not.toBeVisible();
  expect(state.writes[0]).toMatchObject({ expectedVersion: 1, expectedDefaultVersion: 1, fullnessMode: 'WEIGHT_ONLY', fullnessWeightKg: '65.125' });
});

async function tenantSetup(page: Page, canManage: boolean, assigned = true) {
  const asset = permanentDeviceAsset({ tenantCode: 'tenant', organizationCode: assigned ? 'org-b' : null, acceptanceStatus: 'PASSED', listStatus: null });
  const application = { applicationUid: '11111111-1111-4111-8111-111111111179', status: 'APPLIED', dispatchState: 'DONE', version: 1 };
  const configuration = {
    deviceCode: asset.deviceCode, versionNo: 3, schemaVersion: 2, runtimeSnapshotPolicyVersion: 1,
    contentSha256: 'a'.repeat(64), mcuPayloadSha256: 'b'.repeat(64), publicationSource: 'STAFF', publishedBy: '测试', publishedAt: now, application,
    device: { mcuHeartbeatIntervalMs: 5000, mcuHeartbeatMissThreshold: 3, doorCloseRetryLimit: 3, continueDeliveryWaitMs: 30000, negativeWeightThresholdGram: 500 },
    ports: [{ portNo: 1, displayName: '纸类', enabled: true, unitPriceYuanPerKg: '0.4500', fullnessMode: 'INFRARED_OR_WEIGHT', fullnessWeightKg: '50.000', deliverySettleDelayMs: 3000, fullnessInitialDelayMs: 5000, fullnessRecheckDelayMs: 10000, doorAutoCloseTimeoutMs: 60000 }],
  };
  const state = { reads: [] as string[], writes: [] as Array<{ path: string; body: Record<string, unknown> }> };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/csrf-token')) return ok(route, { token: 'tenant-csrf', headerName: 'X-CSRF-TOKEN' });
    if (path === '/api/v1/web/platform/auth/sessions/current') return reject(route);
    if (path === '/api/v1/web/auth/sessions/current') return ok(route, {
      sessionUid: '11111111-1111-4111-8111-111111111171', subjectUid: '11111111-1111-4111-8111-111111111172',
      accountType: canManage ? 'TENANT_PRINCIPAL' : 'STAFF', displayName: '租户测试',
      capabilities: ['device.read', 'device.assignment.manage', ...(canManage ? ['device.configuration.manage'] : [])],
      organizations: [{ organizationCode: 'org-a', organizationName: '甲机构' }, { organizationCode: 'org-b', organizationName: '乙机构' }],
      tenantCode: 'tenant', version: 1, authVersion: 1, expiresAt: '2030-01-01T00:00:00Z',
    });
    if (route.request().method() !== 'GET') {
      state.writes.push({ path, body: route.request().postDataJSON() });
      const statusUrl = `/api/v1/web/organizations/org-b/devices/${asset.deviceCode}/configuration-applications/${application.applicationUid}`;
      return route.fulfill({ status: 202, headers: { Location: statusUrl }, json: { code: 'OK', requestId: 'test-publish', data: {
        ...application, versionNo: 4, status: 'PENDING', statusUrl, recommendedPollAfterMs: 2000,
        operationId: route.request().headers()['idempotency-key'], resourceId: application.applicationUid,
      } } });
    }
    state.reads.push(path);
    if (path.endsWith('/device-assets')) return ok(route, { items: [asset], total: 1, page: 1, pageSize: 20 });
    if (path.endsWith('/runtime')) return ok(route, permanentDeviceRuntime(asset.deviceCode));
    if (path.endsWith('/configuration-versions')) return ok(route, { items: [{ ...configuration }], nextBeforeVersionNo: null });
    if (path.endsWith('/configuration-versions/3')) return ok(route, configuration);
    if (path.endsWith('/organizations')) return ok(route, { items: [], total: 0, page: 1, pageSize: 200 });
    return reject(route, 404);
  });
  await page.goto('/devices?organization=org-a');
  await page.getByRole('button', { name: `查看设备 ${asset.hardwareSn}`, exact: true }).click();
  return { ...state, asset };
}

test('tenant publishes to the selected device organization, not the list filter organization', async ({ page }) => {
  const state = await tenantSetup(page, true);
  const drawer = page.locator('.ant-drawer');
  await drawer.getByRole('button', { name: /投口设置与配置记录$/ }).click();
  await drawer.getByRole('button', { name: '基于最新版发布' }).click();
  const modal = page.getByRole('dialog', { name: '发布投口设置与配置记录' });
  await expect(modal.getByLabel('满载重量（千克）')).not.toBeVisible();
  await expect(modal.getByLabel('单价（元/千克）')).not.toBeVisible();
  await modal.getByLabel('修改原因').fill('修改投口名称');
  await modal.getByLabel('显示名').fill('纸类投口');
  await modal.getByRole('button', { name: '发布并自动下发' }).click();
  await expect(modal).not.toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].path).toContain(`/organizations/org-b/devices/${state.asset.deviceCode}/`);
  expect(state.reads.filter(path => path.includes('configuration-versions'))).not.toHaveLength(0);
  expect(state.reads.filter(path => path.includes('configuration-versions')).every(path => path.includes('/organizations/org-b/'))).toBe(true);
  await page.screenshot({ path: test.info().outputPath('tenant-device-configuration.png'), animations: 'disabled' });
});

test('read-only tenant staff can inspect configuration without publishing it', async ({ page }) => {
  const state = await tenantSetup(page, false);
  const drawer = page.locator('.ant-drawer');
  await drawer.getByRole('button', { name: /投口设置与配置记录$/ }).click();
  await expect(drawer.getByRole('button', { name: '基于最新版发布' })).not.toBeVisible();
  expect(state.writes).toHaveLength(0);
  await expect(page.getByRole('menuitem', { name: '设备配置' })).not.toBeVisible();
});

test('an unassigned device cannot open an institution configuration editor', async ({ page }) => {
  const state = await tenantSetup(page, true, false);
  await expect(page.locator('.ant-drawer').getByRole('button', { name: /投口设置与配置记录$/ })).not.toBeVisible();
  expect(state.reads.some(path => path.includes('configuration-versions'))).toBe(false);
});

test('tenant config edits price with advanced settings collapsed and can restore current defaults', async ({ page }) => {
  const state = await platformSetup(page, 'TENANT_PRINCIPAL');
  await page.goto('/configurations/device');
  await expect(page.getByText('使用平台默认', { exact: true })).toBeVisible();
  await expect(page.getByText('0.4500 元/千克', { exact: true })).toBeVisible();
  await expect(page.getByText(/重量减少异常阈值：/)).not.toBeVisible();
  await page.getByRole('button', { name: '修改设置', exact: true }).click();
  const modal = page.getByRole('dialog', { name: '修改设备配置' });
  await expect(modal.getByLabel('重量减少异常阈值（克）', { exact: true })).not.toBeVisible();
  await modal.getByLabel('单价（元/千克）', { exact: true }).fill('0.6789');
  await modal.getByRole('button', { name: /更多设置$/ }).click();
  await modal.getByLabel('重量减少异常阈值（克）', { exact: true }).fill('750');
  await modal.getByLabel('修改原因').fill('调整本租户配置');
  await expect(modal.getByText(/设备确认前可能暂停新投递/)).toBeVisible();
  await modal.getByRole('button', { name: '发布到本租户设备' }).click();
  await expect(modal).not.toBeVisible();
  expect(state.writes[0]).toMatchObject({ expectedVersion: 0, expectedDefaultVersion: 1, configurationMode: 'CUSTOM', unitPriceYuanPerKg: '0.6789', negativeWeightThresholdGram: 750 });
  expect(state.writes[0]).not.toHaveProperty('tenantCode');
  expect(state.writes[0]).not.toHaveProperty('organizationCode');
  await expect(page.getByText('使用租户设置', { exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('tenant-device-policy.png'), animations: 'disabled' });
  await page.getByRole('button', { name: '恢复平台默认', exact: true }).click();
  const restore = page.getByRole('dialog', { name: '恢复平台默认' });
  await expect(restore.getByText('0.4500 元/千克')).toBeVisible();
  await expect(restore.getByText('继续跟随平台默认值')).toBeVisible();
  await restore.getByLabel('修改原因').fill('统一跟随平台');
  await restore.getByRole('button', { name: '恢复并发布' }).click();
  await expect(restore).not.toBeVisible();
  expect(state.writes[1]).toMatchObject({ expectedVersion: 1, expectedDefaultVersion: 1, configurationMode: 'INHERIT' });
  await expect(page.getByText('使用平台默认', { exact: true })).toBeVisible();
});

test('organization-only grants cannot open tenant configuration even through a direct URL', async ({ page }) => {
  const state = await platformSetup(page, 'STAFF', false);
  await page.goto('/configurations/device');
  await expect(page.getByText('403', { exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: '设备配置', exact: true })).not.toBeVisible();
  expect(state.writes).toHaveLength(0);
});

test('tenant-scoped staff grants expose the tenant configuration page', async ({ page }) => {
  await platformSetup(page, 'STAFF', true);
  await page.goto('/configurations/fullness');
  await expect(page).toHaveURL(/\/configurations\/device$/);
  await expect(page.getByText('租户统一配置', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: '修改设置', exact: true })).toBeEnabled();
});

test('policy errors remain visible and retry loads the settings again', async ({ page }) => {
  await platformSetup(page, 'TENANT_PRINCIPAL');
  let unavailable = true;
  await page.route('**/api/v1/web/device-configuration-policy', route => unavailable ? reject(route, 503) : route.fallback());
  await page.goto('/configurations/device');
  await expect(page.getByRole('button', { name: /重\s*试/ })).toBeVisible();
  await expect(page.getByRole('button', { name: '修改设置', exact: true })).toBeDisabled();
  unavailable = false;
  await page.getByRole('button', { name: /重\s*试/ }).click();
  await expect(page.getByRole('button', { name: '修改设置', exact: true })).toBeEnabled();
});
