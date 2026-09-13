import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  acceptCleanBagRecoveryIntent,
  canDiscardCleanOperationIntentForRecovery,
  classifyRecoveryBag,
  cleanBagRecoveryFailureDisposition,
  cleanBagRecoveryNextStep,
  newPendingCleanBagRecoveryIntent,
  rememberCleanBagRecoveryIntent,
  restoreCleanBagRecoveryIntent,
} from '../miniprogram/miniprogram/utils/clean-bag-recovery-intent.ts';

const OLD_BAG =
  'EB1_K1_000G40R40M30E209185GR38E1W_GRQ320Z8YDWC8V49M7W0';
const NEW_BAG =
  'EB1_K1_000G40R40M30E209185GR38E1X_GRQ320Z8YDWC8V49M7W0';
const THIRD_BAG =
  'EB1_K1_000G40R40M30E209185GR38E1Y_GRQ320Z8YDWC8V49M7W0';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

test('interrupted clean recovery accepts only the original or reserved bag', () => {
  assert.equal(
    classifyRecoveryBag(OLD_BAG, NEW_BAG, OLD_BAG),
    'RETAIN_OLD_BAG',
  );
  assert.equal(
    classifyRecoveryBag(OLD_BAG, NEW_BAG, NEW_BAG),
    'USE_RESERVED_NEW_BAG',
  );
  assert.equal(classifyRecoveryBag(OLD_BAG, NEW_BAG, THIRD_BAG), null);
  assert.equal(classifyRecoveryBag(null, NEW_BAG, OLD_BAG), null);
});

test('recovery follows backend actions without turning ABORTED into clean completion', () => {
  const storage = new Map();
  const previousWx = globalThis.wx;
  globalThis.wx = {
    setStorageSync: (key, value) => storage.set(key, structuredClone(value)),
    getStorageSync: (key) => storage.get(key),
    removeStorageSync: (key) => storage.delete(key),
  };
  try {
    const operationUid = '123e4567-e89b-42d3-a456-426614174001';
    const intent = newPendingCleanBagRecoveryIntent({
      deviceCode: 'Dv_1234567890abcdefghijklmn',
      portNo: 2,
      operationUid,
      expectedOperationVersion: 7,
      originalBagQr: OLD_BAG,
      reservedNewBagQr: NEW_BAG,
      actualBagQr: NEW_BAG,
      emptyBagConfirmed: true,
      reason: '现场已核对清运中断后的实际袋',
      idempotencyKey: '123e4567-e89b-42d3-b456-426614174002',
    });
    const accepted = acceptCleanBagRecoveryIntent(intent, {
      recoveryUid: '123e4567-e89b-42d3-a456-426614174003',
      operationUid,
      state: 'BASELINE_PENDING',
      decision: 'USE_RESERVED_NEW_BAG',
      actualBagQr: NEW_BAG,
      baselineMeasurementUid: '123e4567-e89b-42d3-a456-426614174004',
      baselineTaskUid: '123e4567-e89b-42d3-a456-426614174005',
      nextAction: 'WAIT_FOR_EMPTY_BAG_BASELINE',
    });
    assert.equal(accepted.lastState, 'BASELINE_PENDING');
    assert.equal(restoreCleanBagRecoveryIntent()?.lastState, 'BASELINE_PENDING');

    assert.equal(
      cleanBagRecoveryNextStep('ABORTED', ['SCAN_ACTUAL_BAG'], false),
      'SCAN',
    );
    assert.equal(
      cleanBagRecoveryNextStep(
        'ABORTED',
        ['WAIT_FOR_EMPTY_BAG_BASELINE'],
        true,
      ),
      'WAIT',
    );
    assert.equal(
      cleanBagRecoveryNextStep(
        'ABORTED',
        ['RETRY_EMPTY_BAG_BASELINE'],
        true,
      ),
      'RETRY',
    );
    assert.equal(cleanBagRecoveryNextStep('ABORTED', [], true), 'COMPLETED');
    assert.equal(
      cleanBagRecoveryNextStep('ABORTED', ['CONTACT_SUPPORT'], true),
      'UNAVAILABLE',
    );
    assert.equal(
      cleanBagRecoveryNextStep('ABORTED', ['FUTURE_ACTION'], true),
      'UNAVAILABLE',
    );
    assert.equal(cleanBagRecoveryNextStep('ABORTED', [], false), 'UNAVAILABLE');
    assert.equal(cleanBagRecoveryNextStep('COMPLETED', [], true), 'UNAVAILABLE');
  } finally {
    globalThis.wx = previousWx;
  }
});

test('recovery never discards an unrelated or still-active clean intent', () => {
  const operationUid = '123e4567-e89b-42d3-a456-426614174001';
  const otherOperationUid = '123e4567-e89b-42d3-a456-426614174009';
  assert.equal(
    canDiscardCleanOperationIntentForRecovery(
      operationUid,
      operationUid,
      'ABORTED',
    ),
    true,
  );
  assert.equal(
    canDiscardCleanOperationIntentForRecovery(
      otherOperationUid,
      operationUid,
      'ABORTED',
    ),
    false,
  );
  assert.equal(
    canDiscardCleanOperationIntentForRecovery(
      operationUid,
      operationUid,
      'IN_PROGRESS',
    ),
    false,
  );
});

test('all deterministic recovery 409 responses clear and refresh authority', () => {
  for (const code of [
    'CLEAN.RECOVERY_VERSION_CHANGED',
    'CLEAN.RECOVERY_STATE_CHANGED',
    'CLEAN.RECOVERY_NOT_REQUIRED',
    'CLEAN.RECOVERY_ALREADY_CONFIRMED',
    'CLEAN.RECOVERY_INTERLOCK_CHANGED',
    'CLEAN.CURRENT_BAG_CHANGED',
    'CLEAN.OLD_BAG_BASELINE_CHANGED',
    'CLEAN.RESERVED_BAG_CHANGED',
    'CLEAN.RECOVERY_CAUSE_UNSUPPORTED',
    'CLEAN.RECOVERY_EVIDENCE_MISSING',
  ]) {
    assert.equal(cleanBagRecoveryFailureDisposition(409, code), 'REFRESH');
  }
  assert.equal(
    cleanBagRecoveryFailureDisposition(409, 'CLEAN.FUTURE_CONFLICT'),
    'REFRESH',
  );
  assert.equal(
    cleanBagRecoveryFailureDisposition(422, 'CLEAN.BAG_CODE_INVALID'),
    'RESCAN',
  );
  assert.equal(
    cleanBagRecoveryFailureDisposition(0, 'COMMON.NETWORK_ERROR'),
    'RETAIN',
  );
});

test('recovery persists the complete POST body and UUIDv4 before transport', () => {
  const storage = new Map();
  const previousWx = globalThis.wx;
  globalThis.wx = {
    setStorageSync: (key, value) => storage.set(key, structuredClone(value)),
    getStorageSync: (key) => storage.get(key),
    removeStorageSync: (key) => storage.delete(key),
  };
  try {
    const operationUid = '123e4567-e89b-42d3-a456-426614174001';
    const idempotencyKey = '123e4567-e89b-42d3-b456-426614174002';
    const intent = newPendingCleanBagRecoveryIntent({
      deviceCode: 'Dv_1234567890abcdefghijklmn',
      portNo: 2,
      operationUid,
      expectedOperationVersion: 7,
      originalBagQr: OLD_BAG,
      reservedNewBagQr: NEW_BAG,
      actualBagQr: NEW_BAG,
      emptyBagConfirmed: true,
      reason: '现场已核对清运中断后的实际袋',
      idempotencyKey,
    });
    rememberCleanBagRecoveryIntent(intent);

    assert.deepEqual(restoreCleanBagRecoveryIntent()?.body, {
      actualBagQr: NEW_BAG,
      actualBagConfirmed: true,
      emptyBagConfirmed: true,
      expectedOperationVersion: 7,
      reason: '现场已核对清运中断后的实际袋',
    });
    assert.equal(restoreCleanBagRecoveryIntent()?.idempotencyKey, idempotencyKey);
    assert.equal(restoreCleanBagRecoveryIntent()?.decision, 'USE_RESERVED_NEW_BAG');
    assert.throws(() => newPendingCleanBagRecoveryIntent({
      deviceCode: 'Dv_1234567890abcdefghijklmn',
      portNo: 2,
      operationUid,
      expectedOperationVersion: 7,
      originalBagQr: OLD_BAG,
      reservedNewBagQr: NEW_BAG,
      actualBagQr: NEW_BAG,
      emptyBagConfirmed: false,
      reason: '现场已核对清运中断后的实际袋',
      idempotencyKey,
    }), /空袋/);
  } finally {
    globalThis.wx = previousWx;
  }
});

test('clean API posts recovery body with the caller supplied idempotency key', () => {
  const api = source('../miniprogram/miniprogram/api/clean.ts');
  assert.match(api, /export function recoverInterruptedCleanBag/);
  assert.match(
    api,
    /clean-operations\/\$\{\s*encodeURIComponent\(operationUid\)\s*\}\/bag-recoveries/,
  );
  assert.match(api, /http\.post<InterruptedCleanBagRecoveryAccepted>/);
  assert.match(api, /idempotencyKey/);
  assert.doesNotMatch(api, /requestAccepted<InterruptedCleanBagRecoveryAccepted>/);
});

test('operation page scans and confirms recovery before POST, then only polls baseline', () => {
  const page = source(
    '../miniprogram/miniprogram/pages/clean-operation/clean-operation.ts',
  );
  const markup = source(
    '../miniprogram/miniprogram/pages/clean-operation/clean-operation.wxml',
  );
  const recoveryRules = source(
    '../miniprogram/miniprogram/utils/clean-bag-recovery-intent.ts',
  );
  const recoverySubmit = page.slice(page.indexOf('async onConfirmRecovery'));

  assert.match(page, /onOpenRecovery/);
  assert.match(page, /wx\.scanCode\(\{/);
  assert.match(page, /classifyRecoveryBag/);
  assert.match(page, /wx\.showModal\(\{/);
  assert.match(page, /canDiscardCleanOperationIntentForRecovery/);
  assert.match(page, /cleanBagRecoveryFailureDisposition/);
  assert.match(recoveryRules, /status === 409/);
  assert.match(page, /if \(this\.recoverySubmitRequest\)/);
  assert.ok(
    recoverySubmit.indexOf('rememberCleanBagRecoveryIntent(intent)')
      < recoverySubmit.indexOf('await this.submitRecoveryIntent()'),
  );
  assert.match(page, /businessOperationPollDelay\(\)/);
  assert.match(page, /stage: 'recovery-retry'/);
  assert.doesNotMatch(markup, /input[^>]+actualBag|手填袋码|手动输入袋码/);
  assert.match(markup, /原清运仍保持中止/);
  assert.match(markup, /确认袋内为空/);
  assert.match(markup, /袋状态已确认/);
  assert.doesNotMatch(markup, /恢复清运完成/);
});
