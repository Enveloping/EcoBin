import request from './request';
import type { CommandIntent } from './commandIntent';

export interface McuFirmwareRelease {
  releaseUid: string;
  firmwareVersion: string;
  firmwareVersionCode: number;
  firmwareIdentityHex: string;
  hardwareCompatibility: string;
  fixedFrameRevision: number;
  objectKey: string;
  packageSha256: string;
  packageSize: number;
  status: 'READY' | 'PROMOTED' | 'ARCHIVED';
  releaseNotes?: string;
  createdBy: string;
  promotedBy?: string;
  promotedAt?: string;
  createdAt: string;
}

export interface RegisterMcuFirmwareReleaseRequest {
  releaseUid: string;
  firmwareVersion: string;
  firmwareVersionCode: number;
  firmwareIdentityHex: string;
  hardwareCompatibility: 'ECOBIN_MAINBOARD_V1.1';
  fixedFrameRevision: 2;
  objectKey: string;
  packageSha256: string;
  packageSize: number;
  releaseNotes?: string;
}

export type McuFirmwareDeploymentStatus =
  | 'PENDING'
  | 'QUEUED'
  | 'PACKAGE_FETCH_FAILED'
  | 'PREFLIGHT'
  | 'PREPARED'
  | 'FLASHING_TARGET'
  | 'VERIFYING_TARGET'
  | 'ROLLING_BACK'
  | 'VERIFYING_ROLLBACK'
  | 'SUCCEEDED'
  | 'ROLLED_BACK'
  | 'FAILED_LOCKED'
  | 'LOCAL_CANCELLED'
  | 'REJECTED';

export interface McuFirmwareDeployment {
  deploymentUid: string;
  hardwareSn: string;
  tenantCode?: string;
  organizationCode?: string;
  kind: 'VALIDATION' | 'WAVE';
  waveNo: number;
  status: McuFirmwareDeploymentStatus;
  commandUid?: string;
  reliableTaskUid?: string;
  edgeUpdateUid?: string;
  targetAttemptCount: number;
  rollbackAttemptCount: number;
  installedFirmwareVersion?: string;
  installedFirmwareVersionCode?: number;
  installedFirmwareIdentityHex?: string;
  errorCode?: string;
  queuedAt?: string;
  completedAt?: string;
  updatedAt: string;
}

export type McuFirmwareRolloutStatus =
  | 'DRAFT'
  | 'VALIDATING'
  | 'VALIDATION_FAILED'
  | 'AWAITING_PROMOTION'
  | 'ACTIVE'
  | 'COMPLETED'
  | 'STOPPED';

export interface McuFirmwareRollout {
  rolloutUid: string;
  release: McuFirmwareRelease;
  status: McuFirmwareRolloutStatus;
  batchSize: number;
  maximumWaveNo: number;
  currentWaveNo: number;
  validationHardwareSn: string;
  reason: string;
  createdBy: string;
  promotedBy?: string;
  promotedAt?: string;
  stoppedBy?: string;
  stoppedAt?: string;
  stopReason?: string;
  pendingCount: number;
  runningCount: number;
  succeededCount: number;
  rolledBackCount: number;
  failedCount: number;
  createdAt: string;
  updatedAt: string;
  deployments: McuFirmwareDeployment[];
}

export interface PageData<T> {
  items: T[];
  page: number;
  pageSize: number;
  total: number;
}

export interface CreateMcuFirmwareRolloutRequest {
  releaseUid: string;
  validationHardwareSn: string;
  targetHardwareSns: string[];
  batchSize: number;
  reason: string;
}

const base = '/api/v1/web/platform';

export function listMcuFirmwareReleases(
  params: { page?: number; pageSize?: number } = {},
) {
  return request<PageData<McuFirmwareRelease>>({
    url: `${base}/mcu-firmware-releases`,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function registerMcuFirmwareRelease(
  data: RegisterMcuFirmwareReleaseRequest,
  intent: CommandIntent,
) {
  return intent.execute<
    McuFirmwareRelease,
    RegisterMcuFirmwareReleaseRequest
  >({
    url: `${base}/mcu-firmware-releases`,
    method: 'POST',
    data,
  });
}

export function listMcuFirmwareRollouts(
  params: { page?: number; pageSize?: number } = {},
) {
  return request<PageData<McuFirmwareRollout>>({
    url: `${base}/mcu-firmware-rollouts`,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function createMcuFirmwareRollout(
  data: CreateMcuFirmwareRolloutRequest,
  intent: CommandIntent,
) {
  return intent.execute<McuFirmwareRollout, CreateMcuFirmwareRolloutRequest>({
    url: `${base}/mcu-firmware-rollouts`,
    method: 'POST',
    data,
  });
}

export function getMcuFirmwareRollout(rolloutUid: string) {
  return request<McuFirmwareRollout>({
    url: `${base}/mcu-firmware-rollouts/${encodeURIComponent(rolloutUid)}`,
    method: 'GET',
    noStore: true,
  });
}

function rolloutAction(
  rolloutUid: string,
  action: 'validation-starts' | 'promotions' | 'wave-advancements' | 'stoppages',
  reason: string,
  intent: CommandIntent,
) {
  return intent.execute<McuFirmwareRollout, { reason: string }>({
    url:
      `${base}/mcu-firmware-rollouts/${encodeURIComponent(rolloutUid)}`
      + `/${action}`,
    method: 'POST',
    data: { reason },
  });
}

export const startMcuFirmwareValidation = (
  rolloutUid: string,
  reason: string,
  intent: CommandIntent,
) => rolloutAction(rolloutUid, 'validation-starts', reason, intent);

export const promoteMcuFirmwareRollout = (
  rolloutUid: string,
  reason: string,
  intent: CommandIntent,
) => rolloutAction(rolloutUid, 'promotions', reason, intent);

export const advanceMcuFirmwareWave = (
  rolloutUid: string,
  reason: string,
  intent: CommandIntent,
) => rolloutAction(rolloutUid, 'wave-advancements', reason, intent);

export const stopMcuFirmwareRollout = (
  rolloutUid: string,
  reason: string,
  intent: CommandIntent,
) => rolloutAction(rolloutUid, 'stoppages', reason, intent);
