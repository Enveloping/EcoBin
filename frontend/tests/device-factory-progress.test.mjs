import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  buildFactoryProgressSteps,
  factoryFailureGuidance,
  factoryProgressSummary,
  sealStatusLabel,
} from '../web/src/pages/device-management/factoryProgressPresentation.ts';

function progress(overrides = {}) {
  const base = {
    factoryBags: {
      expectedPortCount: 2,
      verifiedCount: 2,
      complete: true,
      revision: 3,
    },
    acceptance: {
      status: 'PASSED',
      generation: 4,
      currentFailureReasons: [],
      lastEvaluatedAt: '2026-08-31T08:00:00Z',
      acceptedAt: '2026-08-31T08:00:00Z',
      authoritativeEvidence: {
        evidenceUid: '10000000-0000-4000-8000-000000000001',
        evaluationStatus: 'PASSED',
        evidenceSha256: 'a'.repeat(64),
        receivedAt: '2026-08-31T08:00:00Z',
      },
      latestEvidence: {
        evidenceUid: '10000000-0000-4000-8000-000000000001',
        evaluationStatus: 'PASSED',
        evidenceSha256: 'a'.repeat(64),
        receivedAt: '2026-08-31T08:00:00Z',
      },
    },
    acceptanceRequest: {
      taskUid: '20000000-0000-4000-8000-000000000001',
      taskState: 'DONE',
      blockedReasonCode: null,
      blockedDiagnostic: null,
      latestAttempt: null,
    },
    seal: {
      status: 'ACKNOWLEDGED',
      generation: 4,
      cancellationReason: null,
      taskUid: '30000000-0000-4000-8000-000000000001',
      taskState: 'DONE',
      blockedReasonCode: null,
      blockedDiagnostic: null,
      latestAttempt: null,
      acknowledgedAt: '2026-08-31T08:01:00Z',
      sealedAt: null,
      cleanupCompletedAt: null,
      completionReceivedAt: null,
    },
    currentStage: 'END_FACTORY_MODE',
    status: 'WAITING_OPERATOR',
    blockingCode: null,
    nextActionCodes: ['CONFIRM_END_FACTORY_MODE'],
    fetchedAt: '2026-08-31T08:01:01Z',
  };
  return { ...base, ...overrides };
}

test('all current acceptance and seal diagnostics have actionable Chinese guidance', () => {
  const codes = [
    'DEVICE_ASSET_UNAVAILABLE',
    'ONENET_NOT_ONLINE',
    'FACTORY_BAGS_INCOMPLETE',
    'UNSUPPORTED_EDGE_SOFTWARE',
    'UNSUPPORTED_EDGE_PROTOCOL',
    'PERSISTENT_STORE_UNHEALTHY',
    'CONFIGURATION_PERSISTENCE_UNHEALTHY',
    'MCU_COMMUNICATION_UNHEALTHY',
    'SENSOR_SELF_TEST_FAILED',
    'PORT_COUNT_MISMATCH',
    'SENSOR_EVIDENCE_DIGEST_EMPTY',
    'CAMERA_CAPTURE_FAILED',
    'CAMERA_COUNT_INSUFFICIENT',
    'CAMERA_CAPTURE_DIGEST_EMPTY',
    'CAMERA_UPLOAD_READBACK_FAILED',
    'CAMERA_UPLOAD_DIGEST_EMPTY',
    'DEVICE_ENTRY_URL_NOT_STORED',
    'DEVICE_ENTRY_URL_SHA256_MISMATCH',
    'ACCEPTANCE_EVIDENCE_MISSING',
    'MACHINE_ACCEPTANCE_FAILED',
    'ACCEPTANCE_REQUEST_BLOCKED',
    'ACCEPTANCE_REQUEST_CANCELLED',
    'FACTORY_SEAL_AUTHORIZATION_REJECTED',
    'ACCEPTANCE_EVIDENCE_NOT_LATEST',
    'FACTORY_SEAL_AUTHORIZATION_CANCELLED',
    'FACTORY_SEAL_TASK_BLOCKED',
    'FACTORY_SEAL_TASK_CANCELLED',
    'FACTORY_SEAL_STATUS_UNKNOWN',
  ];
  for (const code of codes) {
    const guidance = factoryFailureGuidance(code);
    assert.doesNotMatch(guidance.title, /未识别/);
    assert.ok(guidance.action.length >= 10, code);
  }
  assert.match(
    factoryFailureGuidance('A_NEW_SERVER_CODE').title,
    /A_NEW_SERVER_CODE/,
  );
});

test('ACKNOWLEDGED means waiting for the local portal and never means sealed', () => {
  const current = progress();
  const steps = buildFactoryProgressSteps(current);

  assert.equal(steps[3].status, 'finish');
  assert.equal(steps[4].status, 'process');
  assert.equal(steps[5].status, 'wait');
  assert.match(factoryProgressSummary(current).description, /局域网页/);
  assert.match(sealStatusLabel('ACKNOWLEDGED'), /等待局域网页/);
  assert.doesNotMatch(sealStatusLabel('ACKNOWLEDGED'), /^封存完成$/);
});

test('only SEALED completes local confirmation and the terminal node', () => {
  const current = progress({
    seal: {
      ...progress().seal,
      status: 'SEALED',
      sealedAt: '2026-08-31T08:02:00Z',
      cleanupCompletedAt: '2026-08-31T08:02:01Z',
      completionReceivedAt: '2026-08-31T08:02:02Z',
    },
    currentStage: 'FACTORY_SEALED',
    status: 'COMPLETED',
    nextActionCodes: [],
  });
  const steps = buildFactoryProgressSteps(current);

  assert.equal(steps[4].status, 'finish');
  assert.equal(steps[5].status, 'finish');
  assert.equal(factoryProgressSummary(current).type, 'success');
});

test('a cancelled seal task is shown as a blocked authorization step', () => {
  const current = progress({
    seal: {
      ...progress().seal,
      status: 'PENDING',
      taskState: 'CANCELLED',
      acknowledgedAt: null,
    },
    currentStage: 'FACTORY_SEAL_AUTHORIZATION',
    status: 'BLOCKED',
    blockingCode: 'FACTORY_SEAL_TASK_CANCELLED',
  });

  assert.equal(buildFactoryProgressSteps(current)[3].status, 'error');
  assert.equal(factoryProgressSummary(current).type, 'error');
});

test('a missing-evidence decision does not pretend that a P8 request was sent', () => {
  const current = progress({
    acceptance: {
      ...progress().acceptance,
      status: 'FAILED',
      currentFailureReasons: ['ACCEPTANCE_EVIDENCE_MISSING'],
      authoritativeEvidence: null,
      latestEvidence: null,
    },
    acceptanceRequest: {
      taskUid: null,
      taskState: null,
      blockedReasonCode: null,
      blockedDiagnostic: null,
      latestAttempt: null,
    },
    currentStage: 'MACHINE_ACCEPTANCE',
    status: 'BLOCKED',
    blockingCode: 'ACCEPTANCE_EVIDENCE_MISSING',
  });

  const requestStep = buildFactoryProgressSteps(current)[1];
  assert.equal(requestStep.status, 'wait');
  assert.equal(requestStep.description, '等待系统发起');
});

test('historical evidence never advances the current bag revision', () => {
  const current = progress({
    acceptance: {
      ...progress().acceptance,
      status: 'PENDING',
      generation: 5,
      lastEvaluatedAt: null,
      acceptedAt: null,
      authoritativeEvidence: null,
      latestEvidence: {
        evidenceUid: '10000000-0000-4000-8000-000000000001',
        evaluationStatus: 'PASSED',
        evidenceSha256: 'a'.repeat(64),
        receivedAt: '2026-08-31T08:00:00Z',
      },
    },
    acceptanceRequest: {
      taskUid: null,
      taskState: null,
      blockedReasonCode: null,
      blockedDiagnostic: null,
      latestAttempt: null,
    },
    seal: {
      ...progress().seal,
      status: 'NOT_ISSUED',
      generation: 5,
      taskUid: null,
      taskState: null,
      acknowledgedAt: null,
    },
    currentStage: 'MACHINE_ACCEPTANCE',
    status: 'IN_PROGRESS',
    nextActionCodes: ['WAIT_FOR_ACCEPTANCE_REQUEST'],
  });

  const steps = buildFactoryProgressSteps(current);

  assert.equal(steps[1].status, 'wait');
  assert.equal(steps[1].description, '等待系统发起');
  assert.equal(steps[2].status, 'wait');
  assert.equal(steps[2].description, '等待证据');
});

test('drawer polls without overlap and keeps authoritative evidence separate', () => {
  const drawer = readFileSync(new URL(
    '../web/src/pages/device-management/DeviceAssetDrawer.tsx',
    import.meta.url,
  ), 'utf8');
  const api = readFileSync(new URL(
    '../web/src/api/deviceDirectory.ts',
    import.meta.url,
  ), 'utf8');

  assert.match(api, /\/factory-progress/);
  assert.match(api, /noStore:\s*true/);
  assert.match(drawer, /factoryProgressInFlight/);
  assert.match(drawer, /factoryProgressRefreshQueued/);
  assert.match(drawer, /window\.setTimeout[\s\S]*?5_000/);
  assert.match(drawer, /visibilitychange/);
  assert.match(drawer, /data\.seal\.status === 'SEALED'/);
  assert.match(drawer, /acceptance\.authoritativeEvidence/);
  assert.match(drawer, /acceptance\.latestEvidence/);
  assert.doesNotMatch(drawer, /const latest = rows\[0\]/);
  assert.match(drawer, /可信时间（诊断）/);
  assert.match(drawer, /未同步，不影响验收结论/);
  assert.match(drawer, /模拟来源/);
});
