import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  acceptCleanOperationIntent,
  newPendingCleanOperationIntent,
  parseCleaningDeploymentCode,
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

test('cleaning deployment and raw bag QR parsing keep their distinct boundaries', () => {
  assert.equal(parseCleaningDeploymentCode(' Dp_device_01 '), 'Dp_device_01');
  assert.equal(
    parseCleaningDeploymentCode(
      'https://example.test/device?deploymentCode=Dp_device_02',
    ),
    'Dp_device_02',
  );
  assert.equal(parseCleaningDeploymentCode('deploymentCode=bad'), '');

  assert.equal(parseRawBagQr(' BAG_new_001 '), 'BAG_new_001');
  assert.equal(parseRawBagQr('short'), '');
  assert.equal(
    parseRawBagQr('https://example.test/bag?bagQr=BAG_new_001'),
    '',
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
    cleanBlockerText('CLEAN_SOLENOID_UNAVAILABLE'),
    '清运电磁阀不可用',
  );
});

test('clean operation state disposition separates polling and terminal safety paths', () => {
  for (const status of ['PREPARED', 'EDGE_SAVED', 'IN_PROGRESS']) {
    assert.equal(cleanOperationDisposition(status), 'POLL');
  }
  assert.equal(cleanOperationDisposition('COMPLETED'), 'COMPLETED');
  assert.equal(
    cleanOperationDisposition('PRE_OPEN_ENDED'),
    'PRE_OPEN_ENDED',
  );
  assert.equal(
    cleanOperationDisposition('RECOVERY_REQUIRED'),
    'RECOVERY_REQUIRED',
  );
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
      'Dp_device_01',
      2,
      'BAG_new_001',
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
      installedBagQr: 'BAG_new_001',
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
      deploymentCode: 'Dp_device_01',
      portNo: 2,
      removedBagQr: 'BAG_old_001',
      installedBagQr: 'BAG_new_001',
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
    '/api/v1/miniapp/device-deployments/',
    '/api/v1/miniapp/clean-operations/',
    '/api/v1/miniapp/me/clean-records',
  ]) {
    assert.match(api, new RegExp(path.replaceAll('/', '\\/')));
  }
  assert.match(api, /requestAccepted<CleanOperationAccepted>/);
  assert.match(api, /noStore:\s*true/g);
  assert.match(api, /requireRealCleaningSession\(\)/);
  assert.doesNotMatch(api, /\/api\/app\/clean|\bCleanOrder\b|PageResult/);
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
  assert.match(page, /recommendedPollAfterMs/);
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
