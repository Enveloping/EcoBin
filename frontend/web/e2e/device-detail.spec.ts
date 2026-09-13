import { expect, test, type Page, type Route } from '@playwright/test';
import { legacyDeviceManagementStatus, permanentDeviceAsset, permanentDeviceRuntime } from './fixtures/device';

const hardwareSn = 'SN-DETAIL-01';
const remoteUid = '60000000-0000-4000-8000-000000000001';
const timestamp = '2026-09-12T07:00:00.000Z';

function deviceFixture(sn = hardwareSn) {
  const management = {
    ...legacyDeviceManagementStatus(),
    architectureGeneration: 'PERMANENT_V1',
    businessAdmission: 'ACCEPTING',
    compatibility: 'FULLY_COMPATIBLE',
    businessVersionName: 'business-test-1.0',
    observedAt: timestamp,
  };
  const asset = permanentDeviceAsset({
    hardwareSn: sn,
    tenantCode: 'tenant-detail',
    organizationCode: 'org-detail',
    acceptanceStatus: 'PASSED',
    deviceEntryUrl: `https://example.test/entry?device=${sn}`,
    deviceManagement: management,
  });
  asset.installationProfile = {
    ...asset.installationProfile,
    displayName: '东门回收箱',
    address: '东门服务站旁',
  };
  const runtime = { ...permanentDeviceRuntime(asset.deviceCode), deviceManagement: management };
  const factory = {
    hardwareSn: sn,
    factoryBags: { expectedPortCount: 1, verifiedCount: 1, complete: true, revision: 3 },
    acceptance: {
      status: 'PASSED', generation: 1, currentFailureReasons: [],
      lastEvaluatedAt: timestamp, acceptedAt: timestamp,
      authoritativeEvidence: null, latestEvidence: null,
    },
    acceptanceRequest: {
      taskUid: null, taskState: 'DONE', blockedReasonCode: null,
      blockedDiagnostic: null, latestAttempt: null,
    },
    seal: {
      status: 'SEALED', generation: 1, cancellationReason: null,
      taskUid: null, taskState: 'DONE', blockedReasonCode: null,
      blockedDiagnostic: null, latestAttempt: null,
      acknowledgedAt: timestamp, sealedAt: timestamp,
      cleanupCompletedAt: timestamp, completionReceivedAt: timestamp,
    },
    currentStage: 'FACTORY_SEALED', status: 'COMPLETED', blockingCode: null,
    nextActionCodes: [], fetchedAt: timestamp,
  };
  return { asset, runtime, factory };
}

function remoteFixture() {
  return {
    sessionUid: remoteUid, hardwareSn, maintenanceSshKeyUid: 'test-key',
    state: 'OPEN', remotePort: 22001, expiresAt: '2026-09-12T07:30:00.000Z',
    leaseCleanupPending: false, failureCode: null,
    certificate: 'test-public-certificate-only',
    knownHostsLine: 'test-host-key-only', sshCommand: 'ssh test-device-only',
  };
}

async function json(route: Route, data: unknown) {
  await route.fulfill({ json: { code: 'OK', data, requestId: 'device-detail-test' } });
}

async function problem(route: Route, status: number) {
  await route.fulfill({ status, contentType: 'application/problem+json',
    body: JSON.stringify({ status, code: status === 401 ? 'SECURITY.UNAUTHENTICATED' : 'COMMON.ERROR', message: '读取失败', details: {} }),
  });
}

async function setup(page: Page, fixtures = [deviceFixture()]) {
  const state = {
    runtimeFails: false,
    configurationFails: false,
    remoteFails: false,
    remote: null as ReturnType<typeof remoteFixture> | null,
    writes: [] as Array<{ path: string; body: Record<string, unknown> }>,
    custom: undefined as undefined | ((route: Route, path: string) => Promise<boolean>),
  };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.endsWith('/csrf-token')) return json(route, { token: 'device-detail-csrf', headerName: 'X-CSRF-TOKEN' });
    if (method !== 'GET') {
      state.writes.push({ path, body: route.request().postDataJSON() });
      if (path.endsWith('/remote-support-sessions')) {
        state.remote = remoteFixture();
        return json(route, state.remote);
      }
      if (path.endsWith('/closures') && state.remote) {
        state.remote = { ...state.remote, state: 'CLOSING' };
        return json(route, state.remote);
      }
      return problem(route, 400);
    }
    if (path === '/api/v1/web/auth/sessions/current') return problem(route, 401);
    if (path === '/api/v1/web/platform/auth/sessions/current') return json(route, {
      sessionUid: '10000000-0000-4000-8000-000000000051',
      accountType: 'PLATFORM_ADMIN', subjectUid: '20000000-0000-4000-8000-000000000051',
      displayName: '设备页面验收', tenantCode: null,
      capabilities: ['device.read', 'device.manage'], organizations: [],
      expiresAt: '2030-01-01T00:00:00Z', version: 1, authVersion: 1,
    });
    if (state.custom && await state.custom(route, path)) return;
    if (path === '/api/v1/web/platform/tenants') return json(route, { items: [], page: 1, pageSize: 200, total: 0 });
    if (path === '/api/v1/web/platform/device-assets') return json(route, { items: fixtures.map(item => item.asset), page: 1, pageSize: 20, total: fixtures.length });
    if (path === '/api/v1/web/platform/maintenance-ssh-keys') return json(route, [{ maintenanceSshKeyUid: 'test-key', label: '测试维护电脑', fingerprintSha256: 'public-test-fingerprint', status: 'ACTIVE' }]);
    if (path.endsWith('/remote-support-sessions/current') || path === `/api/v1/web/platform/remote-support-sessions/${remoteUid}`) {
      return state.remoteFails ? problem(route, 500) : state.remote ? json(route, state.remote) : problem(route, 404);
    }
    const fixture = fixtures.find(item => path.includes(`/device-assets/${item.asset.hardwareSn}/`));
    if (fixture) {
      if (path.endsWith('/runtime')) return state.runtimeFails ? problem(route, 500) : json(route, fixture.runtime);
      if (path.endsWith('/factory-progress')) return json(route, fixture.factory);
      if (path.endsWith('/technical-issues') || path.endsWith('/acceptance-evidence')) return json(route, []);
      if (path.endsWith('/configuration-versions')) return state.configurationFails ? problem(route, 500) : json(route, { items: [], nextBeforeVersionNo: null });
    }
    return problem(route, 404);
  });
  return state;
}

async function openDevice(page: Page, sn = hardwareSn) {
  await page.getByRole('button', { name: `查看设备 ${sn}`, exact: true }).click();
  const drawer = page.locator('.ant-drawer');
  await expect(drawer.getByText('最近运行状态', { exact: true })).toBeVisible();
  await expect.poll(async () => {
    const box = await drawer.locator('.ant-drawer-content-wrapper').boundingBox();
    return box ? Math.round(box.x + box.width) : 0;
  }).toBe(page.viewportSize()!.width);
  return drawer;
}

test('device defaults keep essentials and reveal records and QR only on request', async ({ page }) => {
  const state = await setup(page);
  await page.goto('/devices');
  const drawer = await openDevice(page);
  await expect(drawer.getByText('最近上报：部件状态正常', { exact: true })).toBeVisible();
  await expect(drawer.getByText('最近重量 1200 克', { exact: true })).toBeVisible();
  await expect(drawer.getByText('接入与封存 · 已完成', { exact: true })).toBeVisible();
  for (const label of ['设备控制板通信', '设备型号', '业务程序版本', '袋码更新次数', '设备问题与安全恢复']) {
    await expect(drawer.getByText(label, { exact: true })).not.toBeVisible();
  }
  await expect(drawer.getByText('business-test-1.0', { exact: true })).not.toBeVisible();
  await expect(drawer.getByText(/example\.test\/entry/)).not.toBeVisible();
  await page.screenshot({ path: test.info().outputPath('device-default.png') });

  const software = drawer.getByRole('button', { name: /软件与管理详情$/, exact: false });
  await software.focus();
  await software.press('Enter');
  await expect(drawer.getByText('business-test-1.0', { exact: true })).toBeVisible();
  await software.press('Enter');
  await drawer.getByRole('button', { name: /设备资料$/, exact: false }).click();
  await expect(drawer.getByText('ECOBIN-V1', { exact: true })).toBeVisible();
  await drawer.getByRole('button', { name: /接入与封存 · 已完成$/, exact: false }).click();
  await expect(drawer.getByRole('group', { name: '设备出厂接入节点链' })).toBeVisible();
  await drawer.getByRole('button', { name: /接入与封存 · 已完成$/, exact: false }).click();
  await drawer.getByRole('button', { name: /设备资料$/, exact: false }).click();
  await drawer.getByRole('button', { name: /设备二维码$/, exact: false }).click();
  const qr = page.getByRole('dialog', { name: '设备二维码', exact: true });
  await expect(qr.getByRole('img', { name: /设备入口二维码$/ })).toBeVisible();
  await expect(qr.getByText(`https://example.test/entry?device=${hardwareSn}`, { exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('device-qr.png'), animations: 'disabled' });
  expect(state.writes).toEqual([]);
});

test('unknown health, port faults, pending enrollment and stale reads remain visible', async ({ page }) => {
  const fixture = deviceFixture();
  fixture.runtime.health.cameraHealth = 'UNKNOWN';
  fixture.runtime.health.mcuLinkStatus = 'FAILED';
  fixture.runtime.ports[0].deliveryDoorActuatorHealth = 'ACTUATOR_FAULT';
  fixture.runtime.ports[0].smokeState = 'ALARM';
  fixture.runtime.ports[0].weightMeasurementStatus = 'UNSTABLE';
  fixture.runtime.deviceManagement.businessAdmission = 'PAUSED';
  fixture.runtime.deviceManagement.reasons = [{ code: 'SMOKE_ALARM', title: '烟雾报警待排查', description: '请到现场检查烟雾来源，恢复安全后再开始业务。', blocksNewBusiness: true }];
  fixture.factory.seal.status = 'ACKNOWLEDGED';
  fixture.factory.currentStage = 'END_FACTORY_MODE';
  fixture.factory.status = 'WAITING_OPERATOR';
  fixture.factory.nextActionCodes = ['CONFIRM_END_FACTORY_MODE'];
  const state = await setup(page, [fixture]);
  state.configurationFails = true;
  await page.goto('/devices');
  const drawer = await openDevice(page);
  for (const text of ['摄像头：未知', '设备控制板通信：故障', '门驱动：驱动机构故障', '烟雾：报警', '重量测量：不稳定', '烟雾报警待排查', '配置读取失败']) {
    await expect(drawer.getByText(text, { exact: true })).toBeVisible();
  }
  await expect(drawer.getByText('最近上报：部件状态正常', { exact: true })).not.toBeVisible();
  await expect(drawer.getByText('袋码更新次数', { exact: true })).not.toBeVisible();
  await expect(drawer.getByText('下一步', { exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('device-faults.png') });
  state.runtimeFails = true;
  await drawer.getByRole('button', { name: /立即刷新$/, exact: false }).click();
  await expect(drawer.getByText('当前状态无法确认', { exact: true })).toBeVisible();
  await expect(drawer.getByText('设备状态刷新失败，以下是上一次成功结果', { exact: true })).toBeVisible();
  await expect(drawer.getByText('摄像头：未知', { exact: true })).toBeVisible();
  expect(state.writes).toEqual([]);
});

test('active maintenance keeps status and close visible while credentials and cleanup stay contextual', async ({ page }) => {
  const state = await setup(page);
  state.remote = remoteFixture();
  await page.goto('/devices');
  const drawer = await openDevice(page);
  const remote = drawer.getByRole('region', { name: '远程维护', exact: true });
  await expect(remote.getByRole('button', { name: /立即关闭$/, exact: false })).toBeVisible();
  await expect(remote.getByText('已开放', { exact: true })).toBeVisible();
  await expect(remote.getByText('test-public-certificate-only', { exact: true })).not.toBeVisible();
  await remote.getByRole('button', { name: /连接与维护详情$/, exact: false }).click();
  await expect(remote.getByText('test-public-certificate-only', { exact: true })).toBeVisible();
  await expect(remote.getByText('ssh test-device-only', { exact: true })).toBeVisible();
  await remote.getByRole('button', { name: /连接与维护详情$/, exact: false }).click();
  state.remoteFails = true;
  await expect(remote.getByText('远程维护状态更新失败，显示上次结果', { exact: true })).toBeVisible({ timeout: 8000 });
  state.remoteFails = false;
  state.remote = { ...remoteFixture(), state: 'CLOSED', leaseCleanupPending: true };
  await expect(remote.getByText('会话已结束，服务器正在确认远程入口已清除', { exact: true })).toBeVisible({ timeout: 8000 });
  await expect(remote.getByRole('button', { name: /立即关闭$/, exact: false })).not.toBeVisible();
  await remote.getByRole('button', { name: /连接与维护详情$/, exact: false }).click();
  await expect(remote.getByRole('button', { name: /开启远程维护$/, exact: false })).not.toBeVisible();
  expect(state.writes).toEqual([]);
});

test('maintenance still submits only after reason and explicit confirmation', async ({ page }) => {
  const state = await setup(page);
  await page.goto('/devices');
  const drawer = await openDevice(page);
  const remote = drawer.getByRole('region', { name: '远程维护', exact: true });
  await remote.getByRole('button', { name: /远程维护$/, exact: false }).click();
  await remote.getByRole('button', { name: /开启远程维护$/, exact: false }).click();
  const opening = page.getByRole('dialog', { name: `开启 ${hardwareSn} 的临时远程维护` });
  await expect(opening.getByText('本次远程连接最长 30 分钟，到期自动关闭。', { exact: true })).toBeVisible();
  expect(state.writes).toEqual([]);
  await opening.getByLabel('维护原因').fill('检查摄像头');
  await opening.getByRole('button', { name: '确认开启', exact: true }).click();
  await expect(opening).not.toBeVisible();
  expect(state.writes[0].body).toEqual({ maintenanceSshKeyUid: 'test-key', lifetimeSeconds: 900, reason: '检查摄像头' });
  await remote.getByRole('button', { name: /立即关闭$/, exact: false }).click();
  const closing = page.getByRole('dialog', { name: '关闭临时远程维护', exact: true });
  await closing.getByLabel('关闭原因').fill('检查结束');
  await closing.getByRole('button', { name: /立即关闭$/, exact: false }).click();
  await expect(closing).not.toBeVisible();
  expect(state.writes[1].body).toEqual({ reason: '检查结束' });
  await expect(remote.getByText('正在关闭', { exact: true }).first()).toBeVisible();
});

test('switching devices resets disclosures and ignores a late configuration response', async ({ page }) => {
  const secondSn = 'SN-DETAIL-02';
  const state = await setup(page, [deviceFixture(), deviceFixture(secondSn)]);
  let pending = false;
  let resolveLate!: () => void;
  const late = new Promise<void>(resolve => { resolveLate = resolve; });
  state.custom = async (route, path) => {
    const base = `/api/v1/web/platform/device-assets/${hardwareSn}`;
    if (path === `${base}/configuration-versions`) {
      await json(route, { items: [{ versionNo: 3, publishedAt: timestamp, publishedBy: 'test', application: { applicationUid: 'test-application', status: 'PENDING' } }], nextBeforeVersionNo: null });
      return true;
    }
    if (path === `${base}/configuration-versions/3`) {
      await json(route, { versionNo: 3, ports: [], device: {} });
      return true;
    }
    if (path.includes(`${base}/configuration-applications/`)) {
      pending = true;
      await late;
      await json(route, { status: 'FAILED', nextActions: [], lastFailureCode: 'LATE_OLD_DEVICE' });
      return true;
    }
    return false;
  };
  await page.goto('/devices');
  let drawer = await openDevice(page);
  await expect.poll(() => pending).toBe(true);
  await drawer.getByRole('button', { name: /设备资料$/, exact: false }).click();
  await drawer.getByRole('button', { name: /软件与管理详情$/, exact: false }).click();
  await drawer.getByRole('button', { name: /部件与时间明细$/, exact: false }).click();
  await drawer.getByRole('button', { name: '关闭', exact: true }).click();
  drawer = await openDevice(page, secondSn);
  resolveLate();
  await expect(drawer.getByText('最近上报：部件状态正常', { exact: true })).toBeVisible();
  for (const label of ['设备型号', '业务程序版本', '设备控制板通信', '配置应用失败']) {
    await expect(drawer.getByText(label, { exact: true })).not.toBeVisible();
  }
  await page.setViewportSize({ width: 768, height: 900 });
  await page.screenshot({ path: test.info().outputPath('device-narrow.png') });
  expect(state.writes).toEqual([]);
});


test('disabled device explains paused work only in its own details', async ({ page }) => {
  const normal = deviceFixture();
  const disabled = deviceFixture('SN-DISABLED-01');
  disabled.asset.lifecycleStatus = 'DISABLED';
  const state = await setup(page, [normal, disabled]);
  await page.goto('/devices');
  let drawer = await openDevice(page);
  await expect(drawer.getByText('设备已禁用，配置和更新待办已暂停，启用后继续处理。', { exact: true })).toHaveCount(0);
  await drawer.getByRole('button', { name: '关闭', exact: true }).click();
  drawer = await openDevice(page, disabled.asset.hardwareSn);
  await expect(drawer.getByText('设备已禁用，配置和更新待办已暂停，启用后继续处理。', { exact: true })).toBeVisible();
  expect(state.writes).toEqual([]);
});


test('retired devices retain factory history without restoration guidance', async ({ page }) => {
  const fixture = deviceFixture();
  fixture.asset.lifecycleStatus = 'RETIRED';
  fixture.factory.seal.status = 'CANCELLED';
  fixture.factory.currentStage = 'DEVICE_ASSET';
  fixture.factory.status = 'BLOCKED';
  fixture.factory.nextActionCodes = ['RESTORE_DEVICE_ASSET'];
  const state = await setup(page, [fixture]);
  await page.goto('/devices');
  const drawer = await openDevice(page);
  await expect(drawer.getByText('先恢复设备资产为正常状态', { exact: true })).toHaveCount(0);
  await drawer.getByRole('button', { name: /接入与封存记录$/ }).click();
  await expect(drawer.getByRole('group', { name: '设备出厂接入节点链' })).toBeVisible();
  await expect(drawer.getByText('先恢复设备资产为正常状态', { exact: true })).toHaveCount(0);
  await expect(drawer.getByText('下一步', { exact: true })).toHaveCount(0);
  expect(state.writes).toEqual([]);
});
