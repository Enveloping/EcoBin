import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  acceptCleanOperationIntent,
  newPendingCleanOperationIntent,
  parseCleaningDeviceCode,
  parseRawBagQr,
  projectCleanOperationIntent,
  rememberCleanOperationIntent,
  restoreCleanOperationIntent,
} from '../miniprogram/miniprogram/utils/clean-operation-intent.ts';
import {
  autoSelectedCleanPortNo,
  cleanBlockerText,
  cleanOperationDisposition,
} from '../miniprogram/miniprogram/utils/cleaning-flow.ts';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

function port(portNo, cleaningAllowed, blockers = []) {
  return {
    portNo,
    displayName: `${portNo}号`,
    currentBagQr: null,
    fullnessStatus: 'UNKNOWN',
    fullnessPercent: null,
    cleaningAllowed,
    blockers,
  };
}

const AUTHENTICATED_BAG =
  'EB1_K1_000G40R40M30E209185GR38E1W_GRQ320Z8YDWC8V49M7W0';

test('cleaning device and raw bag QR parsing keep their distinct boundaries', () => {
  const firstDevice = 'Dv_1234567890abcdefghijklmn';
  const secondDevice = 'Dv_abcdefghijklmnopqrstuvwx';
  assert.equal(parseCleaningDeviceCode(` ${firstDevice} `), firstDevice);
  assert.equal(
    parseCleaningDeviceCode(
      `https://www.jinshoubao.com/device-entry/?deviceCode=${secondDevice}`,
    ),
    secondDevice,
  );
  assert.equal(parseCleaningDeviceCode('deviceCode=bad'), '');
  assert.equal(parseCleaningDeviceCode('Dp_obsolete_device_code'), '');

  assert.equal(parseRawBagQr(` ${AUTHENTICATED_BAG} `), AUTHENTICATED_BAG);
  assert.equal(parseRawBagQr('short'), '');
  assert.equal(
    parseRawBagQr(`https://example.test/bag?bagQr=${AUTHENTICATED_BAG}`),
    '',
  );
  assert.equal(parseRawBagQr('BAG_new_001'), '');
  assert.equal(
    parseRawBagQr(
      'EB1_K1_000G40R40M30E209185GR38E1W_GRQ320Z8YDWC8V49M7W1',
    ),
    // 小程序只做严格外形过滤；真正的 HMAC 防伪校验由后端完成。
    'EB1_K1_000G40R40M30E209185GR38E1W_GRQ320Z8YDWC8V49M7W1',
  );
  assert.equal(parseRawBagQr('BAG code 001'), '');
});

test('only one available clean port is auto-selected and blockers stay explicit', () => {
  assert.equal(
    autoSelectedCleanPortNo([port(1, false), port(2, true)]),
    2,
  );
  assert.equal(
    autoSelectedCleanPortNo([port(1, true), port(2, true)]),
    null,
  );
  assert.equal(autoSelectedCleanPortNo([port(1, false)]), null);
  assert.equal(cleanBlockerText('DEVICE_BUSY'), '设备正在执行其他作业');
  assert.equal(
    cleanBlockerText('CONFIGURATION_NOT_APPLIED'),
    '设备配置尚未完整应用',
  );
  assert.equal(
    cleanBlockerText('DEVICE_SOFTWARE_NOT_ACCEPTING'),
    '设备正在维护，请稍后再发起清运',
  );
  assert.equal(
    cleanBlockerText('FUTURE_BLOCKER'),
    '暂时无法清运，请稍后重试或联系管理员',
  );
});

test('clean operation state disposition separates polling and terminal safety paths', () => {
  for (const status of ['PREPARED', 'EDGE_SAVED', 'IN_PROGRESS']) {
    assert.equal(cleanOperationDisposition(status), 'POLL');
  }
  assert.equal(cleanOperationDisposition('COMPLETED'), 'COMPLETED');
  assert.equal(
    cleanOperationDisposition('PRE_UNLOCK_ENDED'),
    'PRE_UNLOCK_ENDED',
  );
  assert.equal(
    cleanOperationDisposition('RECOVERY_REQUIRED'),
    'RECOVERY_REQUIRED',
  );
  assert.equal(cleanOperationDisposition('ABORTED'), 'ABORTED');
  assert.equal(cleanOperationDisposition('PRE_OPEN_ENDED'), 'UNKNOWN');
  assert.equal(cleanOperationDisposition('FUTURE_STATUS'), 'UNKNOWN');
});

test('pending clean intent is durable before acceptance and keeps its UUIDv4', () => {
  const storage = new Map();
  const previousWx = globalThis.wx;
  globalThis.wx = {
    setStorageSync: (key, value) => storage.set(key, structuredClone(value)),
    getStorageSync: (key) => storage.get(key),
    removeStorageSync: (key) => storage.delete(key),
  };
  try {
    const key = '123e4567-e89b-42d3-a456-426614174000';
    const operationUid = '123e4567-e89b-42d3-b456-426614174001';
    const intent = newPendingCleanOperationIntent(
      'Dv_1234567890abcdefghijklmn',
      2,
      AUTHENTICATED_BAG,
      key,
    );
    rememberCleanOperationIntent(intent);
    assert.equal(restoreCleanOperationIntent().idempotencyKey, key);
    const accepted = acceptCleanOperationIntent(intent, {
      operationId: operationUid,
      resourceId: operationUid,
      operationUid,
      status: 'PREPARED',
      version: 0,
      portNo: 2,
      installedBagQr: AUTHENTICATED_BAG,
      startAuthorizationExpiresAt: '2026-08-01T00:01:00.000Z',
      statusUrl: `/api/v1/miniapp/clean-operations/${operationUid}`,
      recommendedPollAfterMs: 1000,
      nextActions: ['WAIT'],
    });
    assert.equal(accepted.idempotencyKey, key);
    const progressed = projectCleanOperationIntent(accepted, {
      operationUid,
      status: 'IN_PROGRESS',
      version: 2,
      deviceCode: 'Dv_1234567890abcdefghijklmn',
      portNo: 2,
      removedBagQr: 'BAG_old_001',
      installedBagQr: AUTHENTICATED_BAG,
      firstUnlockMayHaveExecuted: true,
      cleanLockDeenergizedConfirmed: false,
      cleanerPhysicalCloseConfirmed: false,
      startAuthorizationExpiresAt: '2026-08-01T00:01:00.000Z',
      executionDeadlineAt: '2026-08-01T00:30:00.000Z',
      completedAt: null,
      cleanRecordNo: null,
      recommendedPollAfterMs: 1750,
      nextActions: ['WAIT'],
    });
    assert.equal(progressed.idempotencyKey, key);
    assert.equal(progressed.lastStatus, 'IN_PROGRESS');
    assert.equal(progressed.recommendedPollAfterMs, 1750);
  } finally {
    globalThis.wx = previousWx;
  }
});

test('miniapp clean API uses only target paths, no-store reads and 202 validation', () => {
  const api = source('../miniprogram/miniprogram/api/clean.ts');
  for (const path of [
    '/api/v1/miniapp/devices/',
    '/api/v1/miniapp/clean-operations/',
    '/api/v1/miniapp/me/clean-devices',
    '/api/v1/miniapp/me/clean-records',
  ]) {
    assert.match(api, new RegExp(path.replaceAll('/', '\\/')));
  }
  assert.match(api, /requestAccepted<CleanOperationAccepted>/);
  assert.match(api, /noStore:\s*true/g);
  assert.match(api, /requireRealCleaningSession\(\)/);
  assert.doesNotMatch(api, /\/api\/app\/clean|\bCleanOrder\b|PageResult/);
  assert.doesNotMatch(api, /device-deployments|deploymentCode/);
});

test('operation page saves before POST, gates phone scan and never links completion to records', () => {
  const page = source(
    '../miniprogram/miniprogram/pages/clean-operation/clean-operation.ts',
  );
  const markup = source(
    '../miniprogram/miniprogram/pages/clean-operation/clean-operation.wxml',
  );
  const confirmBlock = page.slice(page.indexOf('async onConfirmStart'));
  assert.ok(
    confirmBlock.indexOf('rememberCleanOperationIntent(intent)')
      < confirmBlock.indexOf('await this.submitIntent()'),
  );
  assert.match(page, /if \(!session\?\.phoneBound\)/);
  assert.match(page, /pendingBagScanAfterPhone = false/);
  assert.match(page, /isWechatPhoneGrantCancelled\(event\.detail\)/);
  assert.match(page, /onHide\(\)[\s\S]*?clearPollTimer\(\)/);
  assert.match(page, /businessOperationPollDelay/);
  assert.match(page, /schedulePoll\(0\)/);
  assert.match(page, /onPullDownRefresh\(\)/);
  assert.match(page, /if \(this\.pollRequest\) return this\.pollRequest/);
  assert.doesNotMatch(page, /maximumElapsedMs|5 \* 60 \* 1000/);
  assert.doesNotMatch(page, /pages\/clean-records/);
  assert.match(markup, /确认开始清运/);
  assert.match(markup, /open-type="getPhoneNumber"/);
  assert.match(markup, /取消后会停留在投口页面，不会扫码，也不会发起清运请求/);
  assert.match(page, /我的 → 清运记录/);
  assert.doesNotMatch(markup, /手动输入/);
});

test('clean records use cursor recovery and expose a real detail page', () => {
  const list = source(
    '../miniprogram/miniprogram/pages/clean-records/clean-records.ts',
  );
  const detail = source(
    '../miniprogram/miniprogram/pages/clean-record-detail/clean-record-detail.ts',
  );
  const app = JSON.parse(source('../miniprogram/miniprogram/app.json'));
  assert.match(list, /nextCursor/);
  assert.match(list, /COMMON\.INVALID_CURSOR/);
  assert.match(list, /onPullDownRefresh/);
  assert.match(list, /onReachBottom/);
  assert.match(list, /cleanRecordNo=/);
  assert.match(detail, /cleanRecordDetail\(this\.cleanRecordNo, false\)/);
  assert.match(detail, /postCleanDetection/);
  assert.match(detail, /newBaseline/);
  assert.ok(
    app.pages.includes('pages/clean-record-detail/clean-record-detail'),
  );
});

test('clean device lists cover six operational filters and keep scan as the start boundary', () => {
  const list = source(
    '../miniprogram/miniprogram/pages/clean-devices/clean-devices.ts',
  );
  const markup = source(
    '../miniprogram/miniprogram/pages/clean-devices/clean-devices.wxml',
  );
  for (const filter of [
    'ALL',
    'ONLINE',
    'NO_DELIVERY_24H',
    'NO_CLEAN_24H',
    'FULL',
    'FULL_TIMEOUT_2H',
  ]) {
    assert.match(list, new RegExp(`'${filter}'`));
  }
  assert.match(list, /options\.filter \|\| 'ALL'/);
  assert.match(list, /nextCursor/);
  assert.match(list, /COMMON\.INVALID_CURSOR/);
  assert.match(list, /onPullDownRefresh/);
  assert.match(list, /onReachBottom/);
  assert.match(list, /startCleaningEntry\(\)/);
  assert.match(markup, /扫码开始清运/);
  assert.doesNotMatch(markup, /bindtap="onStartDevice"/);
});

test('cleaners edit an independent current installation profile from both device entries', () => {
  const app = JSON.parse(source('../miniprogram/miniprogram/app.json'));
  const api = source('../miniprogram/miniprogram/api/clean.ts');
  const page = source(
    '../miniprogram/miniprogram/pages/device-installation-profile/device-installation-profile.ts',
  );
  const markup = source(
    '../miniprogram/miniprogram/pages/device-installation-profile/device-installation-profile.wxml',
  );
  const styles = source(
    '../miniprogram/miniprogram/pages/device-installation-profile/device-installation-profile.wxss',
  );
  const operation = source(
    '../miniprogram/miniprogram/pages/clean-operation/clean-operation.ts',
  );
  const devices = source(
    '../miniprogram/miniprogram/pages/clean-devices/clean-devices.ts',
  );

  assert.ok(app.pages.includes(
    'pages/device-installation-profile/device-installation-profile',
  ));
  assert.deepEqual(app.requiredPrivateInfos, [
    'getLocation',
    'chooseLocation',
  ]);
  assert.match(api, /\/installation-profile/);
  assert.match(api, /http\.get<DeviceInstallationProfile>/);
  assert.match(api, /http\.put<DeviceInstallationProfile>/);
  assert.match(page, /requireEntryMode\(\['CLEANING'\]\)/);
  assert.match(page, /wx\.getLocation\(\{/);
  assert.match(page, /type:\s*'gcj02'/);
  assert.match(page, /isHighAccuracy:\s*true/);
  assert.match(page, /highAccuracyExpireTime:\s*8000/);
  assert.match(page, /horizontalAccuracy/);
  assert.match(page, /MAX_ACCEPTABLE_LOCATION_ACCURACY_METERS\s*=\s*100/);
  assert.match(page, /定位精度较低/);
  assert.match(page, /未更新设备坐标/);
  assert.match(page, /wx\.chooseLocation\(options\)/);
  assert.match(page, /latitude:\s*Number\(this\.data\.latitude\)/);
  assert.match(page, /longitude:\s*Number\(this\.data\.longitude\)/);
  assert.match(page, /address:\s*locationAddress\(result\.address, result\.name\)/);
  assert.match(page, /expectedVersion:\s*this\.data\.version/);
  assert.match(page, /COMMON\.VERSION_CONFLICT/);
  assert.match(page, /installationProfileUpdated/);
  assert.doesNotMatch(page, /configuration-releases|OneNet|applyConfiguration/);
  assert.match(markup, /坐标系 GCJ-02/);
  assert.match(markup, /获取手机当前位置/);
  assert.match(markup, /bindtap="onChooseLocation"/);
  assert.match(markup, /点击坐标可进入地图微调/);
  assert.match(markup, /地图选择会带回基础地址/);
  assert.doesNotMatch(markup, /打开地图选择位置|拖动选择/);
  assert.match(
    styles,
    /\.coordinate-panel view\.coordinate-action\s*\{[^}]*flex-direction:\s*row;[^}]*\}/s,
  );
  assert.match(
    styles,
    /\.coordinate-panel \.coordinate-action text:first-child\s*\{[^}]*white-space:\s*nowrap;[^}]*\}/s,
  );
  assert.match(markup, /不会通知香橙派或 MCU/);
  assert.doesNotMatch(markup, /bindinput="onLongitude|bindinput="onLatitude/);
  assert.match(operation, /onConfigureInstallation/);
  assert.match(devices, /onConfigureInstallation/);
});
