import type { DeviceAsset, DeviceRuntime } from '@/api/deviceDirectory';

export type DeviceManagementSummary = DeviceAsset['deviceManagement'];
export type DeviceManagementDetail = DeviceRuntime['deviceManagement'];
export type DeviceManagementReason = NonNullable<
  DeviceManagementSummary['primaryReason']
>;
export type DeviceProtocolVersion = NonNullable<
  DeviceManagementDetail['uartProtocol']
>;
type DeviceManagementArchitectureGeneration =
  DeviceManagementSummary['architectureGeneration'];
type DeviceBusinessAdmissionStatus =
  DeviceManagementSummary['businessAdmission'];
type DeviceCompatibilityStatus = DeviceManagementSummary['compatibility'];

export interface DeviceManagementPresentation {
  color: 'success' | 'warning' | 'error' | 'default';
  label: string;
  description: string;
}

type UnknownRecord = Record<string, unknown>;

const LEGACY_DESCRIPTION =
  '尚未切换到新版设备管理程序，本次改造不会改变现有投递和清运；'
  + '开始业务时仍会检查联网、配置、安全和占用状态。';

function record(value: unknown): UnknownRecord | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as UnknownRecord;
}

function nullableString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function nullableBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function nullableInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value)
    ? value
    : null;
}

function statusValue(value: unknown): string | null {
  if (typeof value === 'string') return value;
  return nullableString(record(value)?.status);
}

function architectureGeneration(
  value: unknown,
): DeviceManagementArchitectureGeneration | null {
  if (value === 'LEGACY_DIRECT') return 'LEGACY_DIRECT';
  if (value === 'PERMANENT_V1' || value === 'MANAGED_V1') {
    return 'PERMANENT_V1';
  }
  return null;
}

function admissionStatus(value: unknown): DeviceBusinessAdmissionStatus | null {
  const status = statusValue(value);
  if (status === 'ACCEPTING' || status === 'ACCEPTED') return 'ACCEPTING';
  if (status === 'PAUSED') return 'PAUSED';
  if (status === 'UNKNOWN') return 'UNKNOWN';
  return null;
}

function compatibilityStatus(value: unknown): DeviceCompatibilityStatus | null {
  const status = statusValue(value);
  if (status === 'COMPATIBLE' || status === 'FULLY_COMPATIBLE') {
    return 'FULLY_COMPATIBLE';
  }
  if (
    status === 'LIMITED'
    || status === 'BASE_COMPATIBLE'
    || status === 'BASE_COMPATIBLE_WITH_LIMITS'
  ) {
    return 'BASE_COMPATIBLE';
  }
  if (status === 'INCOMPATIBLE') return 'INCOMPATIBLE';
  if (status === 'UNKNOWN') return 'UNKNOWN';
  return null;
}

function managementReason(value: unknown): DeviceManagementReason | null {
  const source = record(value);
  if (!source) return null;
  const title = nullableString(source.title);
  const description = nullableString(source.description);
  if (!title || !description) return null;
  return {
    code: nullableString(source.code) ?? 'UNSPECIFIED_MANAGEMENT_REASON',
    title: managementOperatorText(title),
    description: managementOperatorText(description),
    blocksNewBusiness: source.blocksNewBusiness === true,
  };
}

function protocolVersion(value: unknown): DeviceProtocolVersion | null {
  const source = record(value);
  if (!source) return null;
  const major = nullableInteger(source.major);
  const minor = nullableInteger(source.minor);
  if (major == null || minor == null || major < 0 || minor < 0) return null;
  return { major, minor };
}

function deviceGateState(
  value: unknown,
): DeviceManagementDetail['deviceGateState'] {
  return value === 'OPEN'
    || value === 'DRAINING'
    || value === 'MAINTENANCE'
    || value === 'LOCKED'
    ? value
    : null;
}

function businessProcessState(
  value: unknown,
): DeviceManagementDetail['businessProcessState'] {
  return value === 'STOPPED'
    || value === 'STARTING'
    || value === 'RUNNING'
    || value === 'FAILED'
    ? value
    : null;
}

function managementSource(value: unknown): UnknownRecord | null {
  return record(record(value)?.deviceManagement);
}

function normalizedSummary(
  source: UnknownRecord,
): DeviceManagementSummary | null {
  const generation = architectureGeneration(source.architectureGeneration);
  if (!generation) return null;
  if (generation === 'LEGACY_DIRECT') {
    return {
      architectureGeneration: generation,
      businessAdmission: null,
      compatibility: null,
      primaryReason: null,
      observedAt: nullableString(source.observedAt),
    };
  }
  return {
    architectureGeneration: generation,
    businessAdmission: admissionStatus(source.businessAdmission),
    compatibility: compatibilityStatus(source.compatibility),
    primaryReason: managementReason(source.primaryReason),
    observedAt: nullableString(source.observedAt),
  };
}

/**
 * Normalizes the additive stage-two field at one presentation boundary. This
 * keeps a rolling frontend/backend deployment tolerant of an older response
 * while ensuring malformed managed-device facts never become a green state.
 */
export function deviceManagementSummary(
  asset: unknown,
): DeviceManagementSummary | null {
  const source = managementSource(asset);
  return source ? normalizedSummary(source) : null;
}

export function deviceManagementDetail(
  runtime: unknown,
): DeviceManagementDetail | null {
  const source = managementSource(runtime);
  if (!source) return null;
  const summary = normalizedSummary(source);
  if (!summary) return null;
  const reasons = Array.isArray(source.reasons)
    ? source.reasons
      .map(managementReason)
      .filter((reason): reason is DeviceManagementReason => reason != null)
    : [];
  return {
    ...summary,
    reasons,
    deviceGateState: deviceGateState(source.deviceGateState),
    managementStateSequence: nullableInteger(source.managementStateSequence),
    communicationAgentVersion: nullableString(source.communicationAgentVersion),
    deviceUpdaterVersion: nullableString(source.deviceUpdaterVersion),
    businessReleaseUid: nullableString(source.businessReleaseUid),
    businessVersionName: nullableString(source.businessVersionName),
    businessReleaseSequence: nullableInteger(source.businessReleaseSequence),
    businessPackageSha256: nullableString(source.businessPackageSha256),
    businessProcessState: businessProcessState(source.businessProcessState),
    businessReady: nullableBoolean(source.businessReady),
    mcuFirmwareVersion: nullableString(source.mcuFirmwareVersion),
    mcuFirmwareIdentityHex: nullableString(source.mcuFirmwareIdentityHex),
    managementTransportProtocol: protocolVersion(
      source.managementTransportProtocol,
    ),
    deviceMaintenanceProtocol: protocolVersion(source.deviceMaintenanceProtocol),
    agentBusinessProtocol: protocolVersion(source.agentBusinessProtocol),
    agentUpdaterProtocol: protocolVersion(source.agentUpdaterProtocol),
    updaterBusinessProtocol: protocolVersion(source.updaterBusinessProtocol),
    uartProtocol: protocolVersion(source.uartProtocol),
    sourceEventUid: nullableString(source.sourceEventUid),
  };
}

export function businessAdmissionPresentation(
  management: DeviceManagementSummary | null,
): DeviceManagementPresentation {
  if (!management || management.architectureGeneration === 'LEGACY_DIRECT') {
    return {
      color: 'default',
      label: '沿用现有业务检查',
      description: LEGACY_DESCRIPTION,
    };
  }
  if (management.businessAdmission === 'ACCEPTING') {
    return {
      color: 'success',
      label: '可以开始新投递和清运',
      description: '这是平台当前记录；每次开始业务时仍会重新检查设备的实时条件。',
    };
  }
  if (management.businessAdmission === 'PAUSED') {
    return {
      color: 'error',
      label: '已暂停新的投递和清运',
      description: '不会取消已经开始的作业；系统仍会接收原作业的完成结果。',
    };
  }
  return {
    color: 'warning',
    label: '暂时无法确认，已按安全规则暂停',
    description: '平台无法确认当前是否适合开始新的物理作业，等待设备重新上报状态。',
  };
}

export function compatibilityPresentation(
  management: DeviceManagementSummary | null,
): DeviceManagementPresentation {
  if (!management || management.architectureGeneration === 'LEGACY_DIRECT') {
    return {
      color: 'default',
      label: '尚未参加新版软件配合检查',
      description: '旧设备继续按现有程序和已验收的软件规则运行。',
    };
  }
  if (management.compatibility === 'FULLY_COMPATIBLE') {
    return {
      color: 'success',
      label: '设备软件配合正常',
      description: '后台、设备业务程序和设备控制板当前可以按已登记的方式协同工作。',
    };
  }
  if (management.compatibility === 'BASE_COMPATIBLE') {
    return {
      color: 'warning',
      label: '投递和清运可用，部分维护功能不可用',
      description: '基础业务仍可使用；不可用的附加能力会在具体原因中说明。',
    };
  }
  if (management.compatibility === 'INCOMPATIBLE') {
    return {
      color: 'error',
      label: '设备软件之间不匹配',
      description: '系统已暂停新的投递和清运，请按具体原因处理后等待设备重新上报。',
    };
  }
  return {
    color: 'warning',
    label: '暂时无法确认设备软件是否匹配',
    description: '系统已暂停新的投递和清运，等待完整、可信的设备软件状态。',
  };
}

export function architectureGenerationLabel(
  management: DeviceManagementSummary | null,
): string {
  return management?.architectureGeneration === 'PERMANENT_V1'
    ? '新版独立设备管理程序'
    : '沿用当前设备程序联网';
}

export function deviceGateStateLabel(value: string | null): string {
  if (!value) return '尚无状态';
  return ({
    OPEN: '允许申请新作业',
    ACCEPTING: '允许申请新作业',
    DRAINING: '正在等待当前作业结束',
    MAINTENANCE: '设备正在维护',
    PAUSED: '暂停新作业',
    CLOSED: '暂停新作业',
    LOCKED: '设备因维护故障暂停，需要人工处理',
    UNKNOWN: '状态暂时无法确认',
  } as Record<string, string>)[value] ?? '状态暂时无法确认';
}

export function businessProcessStateLabel(
  value: string | null,
  ready: boolean | null = null,
): string {
  if (value === 'RUNNING' && ready === true) return '业务程序已就绪';
  if (value === 'RUNNING' && ready === false) {
    return '业务程序已启动，但尚未就绪';
  }
  if (!value) return ready === false ? '业务程序尚未就绪' : '尚无状态';
  return ({
    RUNNING: '业务程序正在运行',
    STARTING: '业务程序正在启动',
    STOPPING: '业务程序正在停止',
    STOPPED: '业务程序已停止',
    FAILED: '业务程序启动失败',
    UNKNOWN: '业务程序状态暂时无法确认',
  } as Record<string, string>)[value] ?? '业务程序状态暂时无法确认';
}

export function formatProtocolVersion(
  value: DeviceProtocolVersion | null,
): string {
  return value ? `${value.major}.${value.minor}` : '尚无数据';
}

export function managementOperatorText(value: string): string {
  const replacements = [
    ['P7', '本机硬件检查'],
    ['P8', '云端自动验收'],
    ['OneNet', '云端设备平台'],
    ['MQTT', '云端连接'],
    ['MCU', '设备控制板'],
    ['COS', '云端文件存储'],
    ['UART', '控制板通信'],
  ] as const;
  const translated = replacements.reduce(
    (text, [internal, friendly]) => text.split(internal).join(friendly),
    value,
  );
  return translated
    .replace(
      /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/gi,
      '设备软件记录',
    )
    .replace(/\b[0-9a-f]{64}\b/gi, '设备软件校验值')
    .replace(/\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g, '技术状态');
}
