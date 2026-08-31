import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  buildFactoryProgressSteps,
  factoryActionLabel,
  factoryFailureGuidance,
  factoryIssueAlertType,
  factoryProgressSummary,
  factoryStageLabel,
  sealStatusLabel,
  taskStateLabel,
  technicalResultLabel,
} from '../web/src/pages/device-management/factoryProgressPresentation.ts';
import {
  operatorFacingTechnicalText,
  runtimeStatusColor,
  runtimeStatusLabel,
  technicalIssueStateLabel,
} from '../web/src/pages/device-management/devicePresentation.ts';

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
    'DEVICE_REJECTED_AUTHORIZATION',
    'ACCEPTANCE_EVIDENCE_NOT_LATEST',
    'FACTORY_SEAL_AUTHORIZATION_CANCELLED',
    'FACTORY_SEAL_TASK_BLOCKED',
    'FACTORY_SEAL_TASK_CANCELLED',
    'FACTORY_SEAL_STATUS_UNKNOWN',
    'DEVICE_OFFLINE',
    'DEVICE_PRESENCE_UNKNOWN',
    'DEVICE_IDENTITY_UNRESOLVED',
    'DEVICE_CONFIRMATION_TIMEOUT',
    'DEVICE_EVIDENCE_TIMEOUT',
    'AUTO_RETRY_EXHAUSTED',
    'PERMANENT_TECHNICAL_FAILURE',
    'AUTHORIZATION_FACT_MISSING',
    'ACCEPTANCE_SNAPSHOT_CHANGED',
  ];
  for (const code of codes) {
    const guidance = factoryFailureGuidance(code);
    assert.doesNotMatch(guidance.title, /未识别/);
    assert.ok(guidance.action.length >= 10, code);
  }
  assert.doesNotMatch(
    factoryFailureGuidance('A_NEW_SERVER_CODE').title,
    /A_NEW_SERVER_CODE/,
  );
});

test('operator-facing progress copy never exposes planning labels or raw states', () => {
  const current = progress();
  const visibleCopy = [
    ...buildFactoryProgressSteps(current).flatMap((step) => [
      step.title,
      step.description,
    ]),
    factoryProgressSummary(current).message,
    factoryProgressSummary(current).description,
    factoryStageLabel(current.currentStage),
    factoryActionLabel(current.nextActionCodes[0]),
    taskStateLabel(current.acceptanceRequest.taskState),
    sealStatusLabel(current.seal.status),
  ].join('\n');

  assert.doesNotMatch(visibleCopy, /\bP[78]\b/);
  assert.doesNotMatch(visibleCopy, /\b(?:PASSED|FAILED|SEALED)\b/);
  assert.doesNotMatch(visibleCopy, /\b[A-Z][A-Z0-9]+_[A-Z0-9_]+\b/);
  assert.equal(factoryStageLabel('A_NEW_STAGE'), '接入状态待确认');
  assert.equal(factoryActionLabel('A_NEW_ACTION'), '请查看处理建议或联系技术支持');
  assert.equal(taskStateLabel('A_NEW_TASK_STATE'), '状态待确认');
  assert.equal(technicalResultLabel('A_NEW_RESULT'), '发送结果待确认');
  assert.equal(runtimeStatusLabel('A_NEW_RUNTIME_STATE'), '状态暂不支持显示');
  assert.equal(runtimeStatusColor('A_NEW_RUNTIME_STATE'), 'warning');
  assert.equal(technicalIssueStateLabel('ACTION_REQUIRED'), '需要管理员处理');
  assert.equal(technicalIssueStateLabel('A_NEW_ISSUE_STATE'), '处理状态待确认');
  assert.doesNotMatch(
    operatorFacingTechnicalText(
      'P8 机器验收由 OneNet 通过 MQTT 发送，等待设备证据进入下一代际（ACTION_REQUIRED）',
    ),
    /P8|机器验收|OneNet|MQTT|设备证据|代际|ACTION_REQUIRED/,
  );
});

test('acceptance request wording distinguishes task creation from delivery facts', () => {
  const acceptance = {
    ...progress().acceptance,
    status: 'PENDING',
    currentFailureReasons: [],
    lastEvaluatedAt: null,
    acceptedAt: null,
    authoritativeEvidence: null,
    latestEvidence: null,
  };
  const withAttempt = (technicalResult) => progress({
    acceptance,
    acceptanceRequest: {
      taskUid: '20000000-0000-4000-8000-000000000001',
      taskState: 'PENDING',
      blockedReasonCode: null,
      blockedDiagnostic: null,
      latestAttempt: technicalResult == null ? null : {
        attemptNo: 1,
        technicalResult,
        httpStatus: null,
        externalErrorCode: null,
        externalRequestId: null,
        diagnostic: null,
        recordedAt: '2026-08-31T08:00:00Z',
      },
    },
    seal: {
      ...progress().seal,
      status: 'NOT_ISSUED',
      taskUid: null,
      taskState: null,
      acknowledgedAt: null,
    },
    currentStage: 'MACHINE_ACCEPTANCE',
    status: 'IN_PROGRESS',
  });

  assert.equal(
    buildFactoryProgressSteps(withAttempt(null))[1].description,
    '系统正在安排发送',
  );
  assert.equal(
    buildFactoryProgressSteps(withAttempt('RETRYABLE_FAILURE'))[1].description,
    '发送暂未成功，系统将自动重试',
  );
  assert.equal(
    buildFactoryProgressSteps(withAttempt('TARGET_OFFLINE'))[1].description,
    '设备离线，恢复连接后系统会继续',
  );
  assert.equal(
    buildFactoryProgressSteps(withAttempt('TECHNICAL_SUCCESS'))[1].description,
    '物联网平台已受理，等待设备确认',
  );
});

test('seal authorization wording distinguishes queued work from platform acceptance', () => {
  const pendingSeal = (technicalResult) => progress({
    seal: {
      ...progress().seal,
      status: 'PENDING',
      taskState: 'PENDING',
      latestAttempt: technicalResult == null ? null : {
        attemptNo: 1,
        technicalResult,
        httpStatus: null,
        externalErrorCode: null,
        externalRequestId: null,
        diagnostic: null,
        recordedAt: '2026-08-31T08:00:00Z',
      },
      acknowledgedAt: null,
    },
    currentStage: 'FACTORY_SEAL_AUTHORIZATION',
    status: 'IN_PROGRESS',
  });

  assert.equal(
    buildFactoryProgressSteps(pendingSeal(null))[3].description,
    '系统正在安排发送',
  );
  assert.equal(
    buildFactoryProgressSteps(pendingSeal('TECHNICAL_SUCCESS'))[3].description,
    '物联网平台已受理，等待设备确认',
  );
  assert.equal(sealStatusLabel('PENDING'), '封存授权处理中');
  assert.equal(taskStateLabel('RUNNING'), '后台正在处理');
});

test('a blocked asset overrides acknowledged seal operator guidance', () => {
  const current = progress({
    currentStage: 'DEVICE_ASSET',
    status: 'BLOCKED',
    blockingCode: 'DEVICE_ASSET_UNAVAILABLE',
    nextActionCodes: ['RESTORE_DEVICE_ASSET'],
  });

  assert.equal(factoryProgressSummary(current).type, 'error');
  assert.doesNotMatch(factoryProgressSummary(current).description, /确认结束出厂模式/);
  assert.equal(buildFactoryProgressSteps(current)[4].status, 'error');
  assert.match(buildFactoryProgressSteps(current)[4].description, /当前不能继续/);
});

test('missing factory bags are an expected operator step, not a red failure', () => {
  const current = progress({
    factoryBags: {
      expectedPortCount: 2,
      verifiedCount: 0,
      complete: false,
      revision: 0,
    },
    acceptance: {
      ...progress().acceptance,
      status: 'PENDING',
      generation: 0,
      lastEvaluatedAt: null,
      acceptedAt: null,
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
    seal: {
      ...progress().seal,
      status: 'NOT_ISSUED',
      generation: 0,
      taskUid: null,
      taskState: null,
      acknowledgedAt: null,
    },
    currentStage: 'FACTORY_BAGS',
    status: 'WAITING_OPERATOR',
    blockingCode: 'FACTORY_BAGS_INCOMPLETE',
    nextActionCodes: ['SCAN_FACTORY_BAGS'],
  });

  assert.equal(factoryProgressSummary(current).type, 'warning');
  assert.match(factoryProgressSummary(current).description, /厂家小程序/);
  assert.equal(factoryIssueAlertType(current), 'warning');
  assert.equal(buildFactoryProgressSteps(current)[0].status, 'process');
});

test('ACKNOWLEDGED means waiting for the local portal and never means sealed', () => {
  const current = progress();
  const steps = buildFactoryProgressSteps(current);

  assert.equal(steps[3].status, 'finish');
  assert.equal(steps[4].status, 'process');
  assert.equal(steps[5].status, 'wait');
  assert.match(factoryProgressSummary(current).description, /设备设置页面/);
  assert.match(sealStatusLabel('ACKNOWLEDGED'), /等待设置页面/);
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

test('a missing result does not pretend that cloud acceptance was requested', () => {
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
  assert.equal(steps[2].description, '等待设备检查结果');
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
  assert.match(drawer, /设备时间状态（仅供排查）/);
  assert.match(drawer, /未同步，不影响验收结论/);
  assert.match(drawer, /模拟来源/);
  assert.doesNotMatch(drawer, /稳定问题代码/);
  assert.match(drawer, /技术诊断（报修时使用）/);
  assert.doesNotMatch(drawer, /<Tag>{issue\.state}<\/Tag>/);
  assert.doesNotMatch(drawer, /问题代码：{issue\.code}/);
  assert.doesNotMatch(drawer, /最近失败：\$\{latestApplication\.lastFailureCode}/);
  assert.doesNotMatch(drawer, /<Tag>{version\.application\.dispatchState}<\/Tag>/);
  assert.match(drawer, /technicalIssueStateLabel\(issue\.state\)/);
  assert.match(drawer, /taskStateLabel\(version\.application\.dispatchState\)/);
  assert.match(drawer, /latestApplication\?\.status === 'FAILED'/);
  assert.doesNotMatch(drawer, /可信运行快照|联网事实|事务中重新检查|全部准入条件/);
  assert.doesNotMatch(drawer, /皮重测量代际|创建新的测量代际|上一代失败事实/);
});

test('device operations distinguish queued work from device execution and hide raw request errors', () => {
  const drawer = readFileSync(new URL(
    '../web/src/pages/device-management/DeviceAssetDrawer.tsx',
    import.meta.url,
  ), 'utf8');
  const directory = readFileSync(new URL(
    '../web/src/pages/device-management/index.tsx',
    import.meta.url,
  ), 'utf8');
  const remoteSupport = readFileSync(new URL(
    '../web/src/pages/device-management/RemoteSupportPanel.tsx',
    import.meta.url,
  ), 'utf8');
  const runtimePolicy = readFileSync(new URL(
    '../web/src/pages/device-management/RuntimeSnapshotPolicyModal.tsx',
    import.meta.url,
  ), 'utf8');
  const presentation = readFileSync(new URL(
    '../web/src/pages/device-management/devicePresentation.ts',
    import.meta.url,
  ), 'utf8');
  const errorPresentation = readFileSync(new URL(
    '../web/src/pages/device-management/operatorErrorPresentation.ts',
    import.meta.url,
  ), 'utf8');
  const operatorSources = [
    drawer,
    directory,
    remoteSupport,
    runtimePolicy,
    errorPresentation,
  ];

  assert.match(drawer, /已安排新一轮空袋重量测量，等待设备执行/);
  assert.doesNotMatch(drawer, /已为 .*开始新一轮空袋重量测量/);
  assert.match(directory, /设备联网并完成初始袋登记后，系统会自动验收/);
  assert.doesNotMatch(directory, /设备联网后会自动验收/);
  assert.match(remoteSupport, /远程协助请求已登记，系统正在安排发送给设备/);
  assert.match(remoteSupport, /CONNECTING: \{ label: '系统正在安排设备连接'/);
  assert.doesNotMatch(remoteSupport, /已交给物联网平台|等待设备建立隧道/);
  assert.match(remoteSupport, /CREDENTIALS_INVALID/);
  assert.match(remoteSupport, /SSH_NOT_AVAILABLE/);
  assert.match(remoteSupport, /CONNECT_TIMEOUT/);
  assert.match(remoteSupport, /remoteSupportFailureCopy\(session\.failureCode\)/);
  assert.match(presentation, /PENDING: '设备功能检查尚未完成'/);
  assert.equal(
    drawer.match(/await onReevaluateAcceptance\(asset\)/g)?.length,
    2,
  );
  assert.match(
    drawer,
    /onClick=\{async \(\) => \{[\s\S]*?await onReevaluateAcceptance\(asset\);[\s\S]*?catch \(error\) \{[\s\S]*?message\.error\(errorMessage\(error\)\)/,
  );
  assert.match(
    drawer,
    /const reevaluateAcceptanceFromIssue[\s\S]*?await onReevaluateAcceptance\(asset\);[\s\S]*?catch \(error\) \{[\s\S]*?message\.error\(errorMessage\(error\)\)/,
  );
  const reevaluateCallback = directory.slice(
    directory.indexOf('onReevaluateAcceptance={async'),
    directory.indexOf('onChanged={reload}'),
  );
  assert.doesNotMatch(reevaluateCallback, /catch\s*\(|message\.error/);

  for (const source of operatorSources) {
    assert.doesNotMatch(source, /技术支持编号：\$\{error\.requestId\}/);
    assert.doesNotMatch(source, /\$\{error\.message\}/);
    assert.doesNotMatch(source, /Error \? error\.message/);
  }
});

test('operator-managed device requests stay silent and rollout copy describes generated settings', () => {
  const deviceApi = readFileSync(new URL(
    '../web/src/api/deviceDirectory.ts',
    import.meta.url,
  ), 'utf8');
  const remoteSupportApi = readFileSync(new URL(
    '../web/src/api/remoteSupport.ts',
    import.meta.url,
  ), 'utf8');
  const remoteSupportPanel = readFileSync(new URL(
    '../web/src/pages/device-management/RemoteSupportPanel.tsx',
    import.meta.url,
  ), 'utf8');
  const runtimePolicy = readFileSync(new URL(
    '../web/src/pages/device-management/RuntimeSnapshotPolicyModal.tsx',
    import.meta.url,
  ), 'utf8');
  const directory = readFileSync(new URL(
    '../web/src/pages/device-management/index.tsx',
    import.meta.url,
  ), 'utf8');
  const identityApi = readFileSync(new URL(
    '../web/src/api/identityDirectory.ts',
    import.meta.url,
  ), 'utf8');
  const directoryScope = readFileSync(new URL(
    '../web/src/pages/identity/useDirectoryScope.ts',
    import.meta.url,
  ), 'utf8');
  const organizationScope = readFileSync(new URL(
    '../web/src/pages/identity/useOrganizationScope.ts',
    import.meta.url,
  ), 'utf8');

  for (const [label, source] of [
    ['device directory', deviceApi],
    ['remote support', remoteSupportApi],
  ]) {
    const requestCount = source.match(/\burl:/g)?.length ?? 0;
    const silentCount = source.match(/\bsilent:\s*true/g)?.length ?? 0;
    assert.equal(silentCount, requestCount, `${label} has an unsilenced request`);
  }
  assert.match(remoteSupportPanel, /listMaintenanceSshKeys\(\{ silent: true \}\)/);
  assert.match(identityApi, /listIdentityTenants[\s\S]*?silent: options\.silent/);
  assert.match(identityApi, /listOrganizations[\s\S]*?silent: options\.silent/);
  assert.match(
    directoryScope,
    /listAllIdentityTenants\(\{\}, \{ silent: true \}\)[\s\S]*?租户列表暂时无法读取/,
  );
  assert.match(
    organizationScope,
    /listAllOrganizations\([\s\S]*?\{ silent: true \}[\s\S]*?机构列表暂时无法读取/,
  );

  assert.match(runtimePolicy, /PENDING: '等待生成设备设置'/);
  assert.match(runtimePolicy, /RUNNING: '正在生成设备设置'/);
  assert.match(runtimePolicy, /DONE: '本轮设备设置已生成'/);
  assert.match(runtimePolicy, /设备设置生成进度/);
  assert.doesNotMatch(runtimePolicy, /正在下发|下发完成|自动下发进度/);
  assert.doesNotMatch(directory, /机器验收/);
  assert.match(directory, /设备功能检查/);
});
