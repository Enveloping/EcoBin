import { expect, test, type Page } from '@playwright/test';
import { permanentDeviceAsset } from './fixtures/device';

const sn = '0123456789abcdef-device-001';
const code = 'device-public-identifier-0001';
const timestamp = '2026-09-12T08:00:00.000Z';

function fixtures() {
  return [permanentDeviceAsset({
    hardwareSn: sn, deviceCode: code, expectedPortCount: 3,
    acceptanceStatus: 'PASSED', tenantCode: '示例租户', organizationCode: '东门服务站',
    connectivity: { oneNetConnectionStatus: 'ONLINE', statusObservedAt: timestamp },
    listStatus: { faults: ['控制板通信未连接'], observedAt: timestamp, ports: [
      { portNo: 1, displayName: '纸类', reportedWeightGrams: 12345, weightValueAvailable: true,
        weightSensorHealth: 'OK', weightMeasurementStatus: 'STABLE', observedAt: timestamp,
        weightFull: false, fullnessObservedAt: timestamp, infraredValue: 'BLOCKED', infraredSensorHealth: 'OK', faults: [] },
      { portNo: 2, displayName: '瓶类', reportedWeightGrams: 0, weightValueAvailable: false,
        weightSensorHealth: 'SENSOR_FAULT', weightMeasurementStatus: 'SENSOR_FAULT', observedAt: timestamp,
        weightFull: true, fullnessObservedAt: timestamp, infraredValue: 'CLEAR', infraredSensorHealth: 'SENSOR_FAULT', faults: ['称重传感器故障'] },
      { portNo: 3, displayName: '金属', reportedWeightGrams: 0, weightValueAvailable: true,
        weightSensorHealth: 'OK', weightMeasurementStatus: 'STABLE', observedAt: timestamp,
        weightFull: null, fullnessObservedAt: null, infraredValue: 'CLEAR', infraredSensorHealth: 'OK', faults: [] },
    ] },
  }), permanentDeviceAsset({ assetUid: '30000000-0000-4000-8000-000000000002', hardwareSn: 'NO-REPORT-DEVICE', expectedPortCount: 2 })];
}

async function setup(page: Page) {
  const requests: string[] = [];
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    requests.push(route.request().url());
    const json = (data: unknown) => route.fulfill({ json: { code: 'OK', data, requestId: 'device-list-test' } });
    if (path === '/api/v1/web/auth/sessions/current') return route.fulfill({ status: 401, json: { code: 'SECURITY.UNAUTHENTICATED' } });
    if (path === '/api/v1/web/platform/auth/sessions/current') return json({
      sessionUid: '10000000-0000-4000-8000-000000000051', accountType: 'PLATFORM_ADMIN',
      subjectUid: '20000000-0000-4000-8000-000000000051', displayName: '设备管理', tenantCode: null,
      capabilities: ['device.read', 'device.manage'], organizations: [], expiresAt: '2030-01-01T00:00:00Z', version: 1, authVersion: 1,
    });
    if (path === '/api/v1/web/platform/device-assets') return json({ items: fixtures(), total: 2, page: 1, pageSize: 20 });
    if (path === '/api/v1/web/platform/tenants') return json({ items: [], total: 0, page: 1, pageSize: 200 });
    return json([]);
  });
  await page.goto('/devices');
  await expect(page.getByRole('button', { name: `打开设备 ${sn}`, exact: true })).toBeVisible();
  return requests;
}

test('device list shows compact identity, direct faults and aligned port observations', async ({ page, context }) => {
  const requests = await setup(page);
  const headers = page.getByRole('columnheader');
  for (const title of ['故障原因', '投口重量', '重量满溢', '红外满溢', '归属']) {
    await expect(headers.filter({ hasText: title })).toBeVisible();
  }
  for (const title of ['新业务状态', '型号 / 投口', '出厂验收', '设备状态', '生命周期', '永久归属']) {
    await expect(headers.filter({ hasText: title })).toHaveCount(0);
  }
  const identity = page.getByRole('button', { name: `打开设备 ${sn}`, exact: true });
  await expect(identity).toHaveText('01234567…');
  await identity.focus();
  await expect(page.getByRole('tooltip')).toContainText(sn);
  await page.mouse.move(0, 0);
  await identity.blur();
  const row = page.getByRole('row').filter({ has: identity });
  await expect(row).toContainText('控制板通信未连接');
  await expect(row).toContainText('2 口：称重传感器故障');
  const metrics = row.locator('.device-list-ports');
  await expect(metrics).toHaveCount(3);
  await expect(metrics.nth(0)).toHaveText('1 口12.345 kg2 口故障3 口0 kg');
  await expect(metrics.nth(1)).toHaveText('1 口未满2 口已满3 口未上报');
  await expect(metrics.nth(2)).toHaveText('1 口已满2 口故障3 口未满');
  for (const portNo of [1, 2, 3]) {
    const tops = await row.locator(`.device-list-port[data-port-no="${portNo}"]`).evaluateAll(elements => elements.map(el => Math.round(el.getBoundingClientRect().top)));
    expect(new Set(tops).size).toBe(1);
  }
  const unknown = page.getByRole('row').filter({ has: page.getByRole('button', { name: '查看设备 NO-REPORT-DEVICE', exact: true }) });
  await expect(unknown.locator('.device-list-ports').nth(0)).toHaveText('1 口未知2 口未知');
  await expect(unknown.locator('.device-list-ports').nth(1)).toHaveText('1 口未上报2 口未上报');
  await expect(unknown.locator('.device-list-ports').nth(2)).toHaveText('1 口未知2 口未知');
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await row.locator('.ant-typography-copy').click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(code);
  expect(requests.filter(path => path.endsWith('/runtime'))).toHaveLength(0);
  await page.screenshot({ path: '../../release-output/device-list-default.png', fullPage: true, animations: 'disabled' });
});

test('optional device columns can be enabled and survive page reload', async ({ page }) => {
  await setup(page);
  await page.locator('#main-content').getByRole('img', { name: 'setting', exact: true }).click();
  const popover = page.locator('.ant-popover:visible');
  for (const title of ['型号 / 投口', '出厂验收', '设备状态']) {
    const node = popover.locator('.ant-tree-treenode').filter({ has: page.getByText(title, { exact: true }) });
    await node.locator('.ant-tree-checkbox').click();
  }
  await page.mouse.click(400, 80);
  for (const title of ['型号 / 投口', '出厂验收', '设备状态']) {
    await expect(page.getByRole('columnheader', { name: title, exact: true })).toBeVisible();
  }
  await page.reload();
  for (const title of ['型号 / 投口', '出厂验收', '设备状态']) {
    await expect(page.getByRole('columnheader', { name: title, exact: true })).toBeVisible();
  }
});


test('device status filter exposes retired history on demand', async ({ page }) => {
  const requests = await setup(page);
  const listRequests = () => requests.map(value => new URL(value))
    .filter(url => url.pathname === '/api/v1/web/platform/device-assets');
  expect(listRequests().at(-1)?.searchParams.has('lifecycleStatus')).toBe(false);
  const selector = page.locator('.ant-select').filter({ has: page.getByLabel('设备状态', { exact: true }) });
  for (const [label, value] of [['已报废', 'RETIRED'], ['全部', 'ALL']]) {
    await selector.click();
    await page.locator('.ant-select-item-option-content').getByText(label, { exact: true }).click();
    await page.getByRole('button', { name: '查 询', exact: true }).click();
    await expect.poll(() => listRequests().at(-1)?.searchParams.get('lifecycleStatus')).toBe(value);
  }
  await page.getByRole('button', { name: '重 置', exact: true }).click();
  await expect.poll(() => listRequests().at(-1)?.searchParams.has('lifecycleStatus')).toBe(false);
});
