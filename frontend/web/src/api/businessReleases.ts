import type { CommandIntent } from './commandIntent';
import request from './request';

export interface PageData<T> {
  items: T[];
  page: number;
  pageSize: number;
  total: number;
}

export interface BusinessReleaseReadiness {
  artifactStorageAvailable: boolean;
  artifactStorageMessage: string;
  signingKeysAvailable: boolean;
  signingKeysMessage: string;
  remoteDispatchEnabled: boolean;
  dispatchMessage: string;
}

export interface BusinessReleaseCompatibility {
  packageFormatVersion: number;
  backendCommandContractVersion: number;
  deviceEventContractVersion: number;
  communicationBusinessProtocol: string;
  updaterBusinessProtocol: string;
  uartProtocol: string;
  requiredMcuCapabilities: string;
  providedBusinessCapabilities: string;
}

export interface BusinessReleaseAction {
  action: string;
  actionLabel: string;
  resultingStatus: string;
  resultingStatusLabel: string;
  requestedBy: string;
  reason: string;
  createdAt: string;
}

export interface BusinessRelease {
  releaseUid: string;
  versionName: string;
  releaseSequence: number;
  status: string;
  statusLabel: string;
  statusDescription: string;
  packageObjectKey: string;
  signatureObjectKey: string;
  packageSha256?: string;
  packageSize?: number;
  signatureSha256?: string;
  signingKeyId?: string;
  artifactUploaded: boolean;
  verificationErrorMessage?: string;
  releaseNotes?: string;
  createdBy: string;
  verifiedBy?: string;
  verifiedAt?: string;
  approvedBy?: string;
  approvedAt?: string;
  suspendedBy?: string;
  suspendedAt?: string;
  suspensionReason?: string;
  retiredBy?: string;
  retiredAt?: string;
  retirementReason?: string;
  createdAt: string;
  updatedAt: string;
  compatibility?: BusinessReleaseCompatibility;
  actions: BusinessReleaseAction[];
}

export interface BusinessDeployment {
  deploymentUid: string;
  hardwareSn: string;
  tenantCode?: string;
  organizationCode?: string;
  kind: string;
  kindLabel: string;
  waveNo: number;
  status: string;
  statusLabel: string;
  cancellationStatus: 'NONE' | 'QUEUED' | 'CANCELLED' | 'TOO_LATE';
  cancellationStatusLabel: string;
  cancelReason?: string;
  cancelRequestedAt?: string;
  cancelResultAt?: string;
  businessAdmissionLabel: string;
  downloadAttemptCount: number;
  targetAttemptCount: number;
  rollbackAttemptCount: number;
  installedVersionName?: string;
  databaseRestored: boolean;
  errorMessage?: string;
  eligibilitySummary: string;
  sourceManagementStateSequence: number;
  currentBusinessReleaseUid: string;
  currentBusinessReleaseSequence: number;
  plannedAt: string;
  queuedAt?: string;
  completedAt?: string;
  updatedAt: string;
}

export interface BusinessRolloutAction {
  action: string;
  actionLabel: string;
  resultingStatus: string;
  resultingStatusLabel: string;
  requestedBy: string;
  reason: string;
  createdAt: string;
}

export interface BusinessRollout {
  rolloutUid: string;
  release: BusinessRelease;
  status: string;
  statusLabel: string;
  batchSize: number;
  maximumWaveNo: number;
  validationHardwareSn: string;
  observationWindowMinutes: number;
  downloadTimeoutMinutes: number;
  drainTimeoutMinutes: number;
  maximumRetryCount: number;
  remoteDispatchEnabled: boolean;
  reason: string;
  createdBy: string;
  stoppedBy?: string;
  stoppedAt?: string;
  stopReason?: string;
  createdAt: string;
  updatedAt: string;
  deployments: BusinessDeployment[];
  actions: BusinessRolloutAction[];
}

export interface CreateBusinessReleaseRequest {
  versionName: string;
  releaseNotes?: string;
}

export interface CreateBusinessRolloutRequest {
  releaseUid: string;
  validationHardwareSn: string;
  targetHardwareSns: string[];
  batchSize: number;
  reason: string;
}

const base = '/api/v1/web/platform/business-releases';

export function getBusinessReleaseReadiness() {
  return request<BusinessReleaseReadiness>({
    url: `${base}/readiness`,
    method: 'GET',
    noStore: true,
  });
}

export function listBusinessReleases(
  params: { page?: number; pageSize?: number } = {},
) {
  return request<PageData<BusinessRelease>>({
    url: base,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getBusinessRelease(releaseUid: string) {
  return request<BusinessRelease>({
    url: `${base}/${encodeURIComponent(releaseUid)}`,
    method: 'GET',
    noStore: true,
  });
}

export function createBusinessRelease(
  data: CreateBusinessReleaseRequest,
  intent: CommandIntent,
) {
  return intent.execute<BusinessRelease, CreateBusinessReleaseRequest>({
    url: base,
    method: 'POST',
    data,
  });
}

export function uploadBusinessReleaseArtifacts(
  releaseUid: string,
  packageFile: File,
  signatureFile: File,
  signingKeyId: string,
  reason: string,
  intent: CommandIntent,
) {
  const data = new FormData();
  data.append('package', packageFile, packageFile.name);
  data.append('signature', signatureFile, signatureFile.name);
  return request<BusinessRelease, FormData>({
    url: `${base}/${encodeURIComponent(releaseUid)}/artifacts`,
    method: 'POST',
    params: { signingKeyId, reason },
    data,
    idempotencyKey: intent.idempotencyKey,
    timeout: 30 * 60 * 1000,
  });
}

function releaseAction(
  releaseUid: string,
  action: 'verifications' | 'approvals' | 'suspensions' | 'resumptions' | 'retirements',
  reason: string,
  intent: CommandIntent,
) {
  return intent.execute<BusinessRelease, { reason: string }>({
    url: `${base}/${encodeURIComponent(releaseUid)}/${action}`,
    method: 'POST',
    data: { reason },
    timeout: action === 'verifications' ? 30 * 60 * 1000 : undefined,
  });
}

export const verifyBusinessRelease = (
  releaseUid: string,
  reason: string,
  intent: CommandIntent,
) => releaseAction(releaseUid, 'verifications', reason, intent);

export const approveBusinessRelease = (
  releaseUid: string,
  reason: string,
  intent: CommandIntent,
) => releaseAction(releaseUid, 'approvals', reason, intent);

export const suspendBusinessRelease = (
  releaseUid: string,
  reason: string,
  intent: CommandIntent,
) => releaseAction(releaseUid, 'suspensions', reason, intent);

export const resumeBusinessRelease = (
  releaseUid: string,
  reason: string,
  intent: CommandIntent,
) => releaseAction(releaseUid, 'resumptions', reason, intent);

export const retireBusinessRelease = (
  releaseUid: string,
  reason: string,
  intent: CommandIntent,
) => releaseAction(releaseUid, 'retirements', reason, intent);

export function listBusinessRollouts(
  params: { page?: number; pageSize?: number } = {},
) {
  return request<PageData<BusinessRollout>>({
    url: `${base}/rollouts`,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getBusinessRollout(rolloutUid: string) {
  return request<BusinessRollout>({
    url: `${base}/rollouts/${encodeURIComponent(rolloutUid)}`,
    method: 'GET',
    noStore: true,
  });
}

export function createBusinessRollout(
  data: CreateBusinessRolloutRequest,
  intent: CommandIntent,
) {
  return intent.execute<BusinessRollout, CreateBusinessRolloutRequest>({
    url: `${base}/rollouts`,
    method: 'POST',
    data,
  });
}

export function stopBusinessRollout(
  rolloutUid: string,
  reason: string,
  intent: CommandIntent,
) {
  return intent.execute<BusinessRollout, { reason: string }>({
    url: `${base}/rollouts/${encodeURIComponent(rolloutUid)}/stoppages`,
    method: 'POST',
    data: { reason },
  });
}

export function startBusinessRolloutValidation(
  rolloutUid: string,
  reason: string,
  intent: CommandIntent,
) {
  return intent.execute<BusinessRollout, { reason: string }>({
    url: `${base}/rollouts/${encodeURIComponent(rolloutUid)}/validation-starts`,
    method: 'POST',
    data: { reason },
  });
}

export function cancelBusinessRolloutDeployment(
  rolloutUid: string,
  deploymentUid: string,
  reason: string,
  intent: CommandIntent,
) {
  return intent.execute<BusinessRollout, { reason: string }>({
    url: `${base}/rollouts/${encodeURIComponent(rolloutUid)}/deployments/${encodeURIComponent(deploymentUid)}/cancellations`,
    method: 'POST',
    data: { reason },
  });
}
