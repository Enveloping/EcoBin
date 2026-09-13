import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  businessAdmissionPresentation,
  businessProcessStateLabel,
  compatibilityPresentation,
  deviceManagementDetail,
  deviceManagementSummary,
  managementOperatorText,
} from '../web/src/pages/device-management/deviceManagementPresentation.ts';

function withManagement(deviceManagement) {
  return { deviceManagement };
}

test('legacy direct devices keep the existing business checks', () => {
  const management = deviceManagementSummary(withManagement({
    architectureGeneration: 'LEGACY_DIRECT',
    businessAdmission: 'UNKNOWN',
    compatibility: 'UNKNOWN',
    observedAt: null,
  }));

  assert.equal(management?.architectureGeneration, 'LEGACY_DIRECT');
  assert.equal(management?.businessAdmission, null);
  assert.equal(management?.compatibility, null);
  assert.equal(
    businessAdmissionPresentation(management).label,
    '沿用现有业务检查',
  );
  assert.equal(
    compatibilityPresentation(management).label,
    '尚未参加新版软件配合检查',
  );
  assert.doesNotMatch(
    businessAdmissionPresentation(management).description,
    /已暂停|无法确认/,
  );
});

test('a frontend deployed before the additive backend field stays neutral', () => {
  assert.equal(deviceManagementSummary({}), null);
  const presentation = businessAdmissionPresentation(null);
  assert.equal(presentation.color, 'default');
  assert.equal(presentation.label, '沿用现有业务检查');
});

test('managed devices present admission separately from compatibility', () => {
  const accepting = deviceManagementSummary(withManagement({
    architectureGeneration: 'PERMANENT_V1',
    businessAdmission: 'ACCEPTING',
    compatibility: 'COMPATIBLE',
    primaryReason: null,
    observedAt: '2026-09-02T04:00:00Z',
  }));
  assert.equal(
    businessAdmissionPresentation(accepting).label,
    '可以开始新投递和清运',
  );
  assert.equal(
    compatibilityPresentation(accepting).label,
    '设备软件配合正常',
  );

  const limited = deviceManagementSummary(withManagement({
    architectureGeneration: 'PERMANENT_V1',
    businessAdmission: { status: 'ACCEPTING' },
    compatibility: { status: 'LIMITED' },
    primaryReason: null,
    observedAt: '2026-09-02T04:00:00Z',
  }));
  assert.equal(
    compatibilityPresentation(limited).label,
    '投递和清运可用，部分维护功能不可用',
  );
  assert.equal(businessAdmissionPresentation(limited).color, 'success');

  const compatibleButPaused = deviceManagementSummary(withManagement({
    architectureGeneration: 'PERMANENT_V1',
    businessAdmission: 'PAUSED',
    compatibility: 'COMPATIBLE',
    primaryReason: null,
    observedAt: '2026-09-02T04:00:00Z',
  }));
  assert.equal(businessAdmissionPresentation(compatibleButPaused).color, 'error');
  assert.equal(compatibilityPresentation(compatibleButPaused).color, 'success');
});

test('incompatible and incomplete managed facts fail closed in operator copy', () => {
  const incompatible = deviceManagementSummary(withManagement({
    architectureGeneration: 'PERMANENT_V1',
    businessAdmission: 'PAUSED',
    compatibility: 'INCOMPATIBLE',
    primaryReason: {
      code: 'BUSINESS_RELEASE_NOT_REGISTERED',
      title: 'P7 未找到 BUSINESS_RELEASE_NOT_REGISTERED',
      description: 'OneNet 收到的记录无法对应后台发布。',
      blocksNewBusiness: true,
    },
    observedAt: '2026-09-02T04:00:00Z',
  }));
  assert.equal(
    compatibilityPresentation(incompatible).label,
    '设备软件之间不匹配',
  );
  assert.doesNotMatch(incompatible?.primaryReason?.title ?? '', /P7|BUSINESS_RELEASE/);
  assert.doesNotMatch(
    incompatible?.primaryReason?.description ?? '',
    /OneNet/,
  );

  const incomplete = deviceManagementSummary(withManagement({
    architectureGeneration: 'PERMANENT_V1',
    businessAdmission: null,
    compatibility: null,
    primaryReason: null,
    observedAt: null,
  }));
  assert.match(
    businessAdmissionPresentation(incomplete).label,
    /暂时无法确认/,
  );
  assert.match(
    compatibilityPresentation(incomplete).description,
    /已暂停新的投递和清运/,
  );
});

test('full runtime status accepts nested statuses and keeps protocols structured', () => {
  const detail = deviceManagementDetail(withManagement({
    architectureGeneration: 'PERMANENT_V1',
    businessAdmission: { status: 'ACCEPTING' },
    compatibility: { status: 'FULLY_COMPATIBLE' },
    primaryReason: null,
    observedAt: '2026-09-02T04:00:00Z',
    reasons: [],
    deviceGateState: 'OPEN',
    businessProcessState: 'RUNNING',
    businessReady: true,
    businessVersionName: '2.0.0',
    communicationAgentVersion: '1.0.0',
    deviceUpdaterVersion: '1.0.0',
    mcuFirmwareVersion: '3.2.1',
    managementTransportProtocol: { major: 1, minor: 2 },
    uartProtocol: { major: 2, minor: 0 },
  }));

  assert.equal(detail?.businessAdmission, 'ACCEPTING');
  assert.equal(detail?.compatibility, 'FULLY_COMPATIBLE');
  assert.equal(detail?.businessReady, true);
  assert.deepEqual(detail?.managementTransportProtocol, { major: 1, minor: 2 });
  assert.deepEqual(detail?.uartProtocol, { major: 2, minor: 0 });
  assert.equal(
    businessProcessStateLabel(
      detail?.businessProcessState ?? null,
      detail?.businessReady ?? null,
    ),
    '业务程序已就绪',
  );
  assert.equal(
    businessProcessStateLabel('RUNNING', false),
    '业务程序已启动，但尚未就绪',
  );
});

test('operator text does not expose identifiers or content hashes', () => {
  const text = managementOperatorText(
    'BUSINESS_RELEASE_UNKNOWN P8 '
    + '123e4567-e89b-42d3-a456-426614174000 '
    + 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  );
  assert.doesNotMatch(text, /BUSINESS_RELEASE_UNKNOWN|P8|123e4567|a{64}/);
  assert.match(text, /技术状态|云端自动验收/);
});

test('device directory and drawer expose only display-only operator controls', () => {
  const directory = readFileSync(new URL(
    '../web/src/pages/device-management/index.tsx',
    import.meta.url,
  ), 'utf8');
  const drawer = readFileSync(new URL(
    '../web/src/pages/device-management/DeviceAssetDrawer.tsx',
    import.meta.url,
  ), 'utf8');

  assert.match(directory, /title: '故障原因'/);
  assert.match(directory, /DeviceFaults/);
  assert.match(directory, /机构无需手动启用设备/);
  assert.doesNotMatch(directory, /联网即可使用/);
  assert.match(drawer, /业务可用状态/);
  assert.match(drawer, /deviceManagementDetail\(runtimeLoad\.data\)/);
  assert.match(drawer, /runtimeUnavailable=\{runtimeLoad\.status === 'error'\}/);
  assert.match(drawer, /下面显示的是上一次成功读取的记录，不能据此开始新的投递或清运/);
  assert.match(drawer, /这是上一次成功读取的状态/);
  assert.match(drawer, /label: '软件与管理详情'/);
  assert.doesNotMatch(drawer, /name=\{\['ports', index, 'unitPriceYuanPerKg'\]\}/);
  assert.match(drawer, /通信版本（报修时使用）/);
  assert.doesNotMatch(drawer, /detail\.(?:businessReleaseUid|businessPackageSha256|sourceEventUid|mcuFirmwareIdentityHex|managementStateSequence)/);
  assert.doesNotMatch(drawer, /\{reason\.code\}/);
  assert.doesNotMatch(drawer, /开始业务程序更新|取消业务程序更新|下发业务发布/);
});
