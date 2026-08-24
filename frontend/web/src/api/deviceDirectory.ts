import request from './request';
import type { CommandIntent } from './commandIntent';
import type { components, operations } from './generated/openapi';

type Schemas = components['schemas'];

export type DeviceAsset = Schemas['DeviceAsset'];
export type DeviceAssetPage = Schemas['DeviceAssetPage'];
export type DeviceAssetLifecycleStatus =
  Schemas['DeviceAssetLifecycleStatus'];
export type DeviceAcceptanceStatus = Schemas['DeviceAcceptanceStatus'];
export type DeviceAcceptanceEvidence = Schemas['DeviceAcceptanceEvidence'];
export type DeviceRuntime = Schemas['DeviceRuntime'];
export type DevicePortRuntime = Schemas['DevicePortRuntime'];
export type CreateDeviceAssetRequest = Schemas['CreateDeviceAssetRequest'];
export type AssignDeviceTenantRequest = Schemas['AssignDeviceTenantRequest'];
export type AssignDeviceOrganizationRequest =
  Schemas['AssignDeviceOrganizationRequest'];
export type DeviceControlRequest = Schemas['DeviceControlRequest'];
export type DeviceConfigurationApplicationStatus =
  Schemas['DeviceConfigurationApplicationStatus'];
export type DeviceConfigurationApplication =
  Schemas['DeviceConfigurationApplication'];
export type DeviceConfigurationAccepted =
  Schemas['DeviceConfigurationAccepted'];
export type DeviceConfigurationReleaseRequest =
  Schemas['DeviceConfigurationReleaseRequest'];
export type DeviceConfigurationResynchronizationRequest =
  Schemas['DeviceConfigurationResynchronizationRequest'];
export type DeviceConfigurationRollForwardRequest =
  Schemas['DeviceConfigurationRollForwardRequest'];
export type DeviceConfigurationVersion =
  Schemas['DeviceConfigurationVersion'];
export type DeviceConfigurationVersionSummary =
  Schemas['DeviceConfigurationVersionSummary'];
export type DeviceConfigurationVersionPage =
  Schemas['DeviceConfigurationVersionPage'];
export type DeviceTechnicalIssue = Schemas['DeviceTechnicalIssue'];
export type BaselineMeasurementAttemptRequest =
  Schemas['BaselineMeasurementAttemptRequest'];
export type BaselineMeasurementAccepted =
  Schemas['BaselineMeasurementAccepted'];
export type RuntimeSnapshotPolicy = Schemas['RuntimeSnapshotPolicy'];
export type RuntimeSnapshotPolicyReleaseRequest =
  Schemas['RuntimeSnapshotPolicyReleaseRequest'];

export type PlatformDeviceAssetListParams = NonNullable<
  operations['listPlatformDeviceAssets']['parameters']['query']
>;
export type TenantDeviceAssetListParams = NonNullable<
  operations['listTenantPermanentDeviceAssets']['parameters']['query']
>;
export type OrganizationDeviceListParams = NonNullable<
  operations['listOrganizationPermanentDevices']['parameters']['query']
>;

export function listPlatformDeviceAssets(
  params: PlatformDeviceAssetListParams = {},
) {
  return request<DeviceAssetPage>({
    url: '/api/v1/web/platform/device-assets',
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getPlatformRuntimeSnapshotPolicy() {
  return request<RuntimeSnapshotPolicy>({
    url: '/api/v1/web/platform/device-runtime-snapshot-policy',
    method: 'GET',
    noStore: true,
  });
}

export function releasePlatformRuntimeSnapshotPolicy(
  data: RuntimeSnapshotPolicyReleaseRequest,
  intent: CommandIntent,
) {
  return intent.execute<
    RuntimeSnapshotPolicy,
    RuntimeSnapshotPolicyReleaseRequest
  >({
    url: '/api/v1/web/platform/device-runtime-snapshot-policy/releases',
    method: 'POST',
    data,
  });
}

export function createPlatformDeviceAsset(
  data: CreateDeviceAssetRequest,
  intent: CommandIntent,
) {
  return intent.execute<DeviceAsset, CreateDeviceAssetRequest>({
    url: '/api/v1/web/platform/device-assets',
    method: 'POST',
    data,
  });
}

export function getPlatformDeviceAsset(hardwareSn: string) {
  return request<DeviceAsset>({
    url: `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}`,
    method: 'GET',
    noStore: true,
  });
}

export function getPlatformDeviceRuntime(hardwareSn: string) {
  return request<DeviceRuntime>({
    url:
      `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}`
      + '/runtime',
    method: 'GET',
    noStore: true,
  });
}

export function assignPlatformDeviceTenant(
  hardwareSn: string,
  data: AssignDeviceTenantRequest,
  intent: CommandIntent,
) {
  return intent.execute<DeviceAsset, AssignDeviceTenantRequest>({
    url:
      `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}`
      + '/tenant-assignments',
    method: 'POST',
    data,
  });
}

export function reevaluateDeviceAcceptance(
  hardwareSn: string,
  intent: CommandIntent,
) {
  return intent.execute<DeviceAsset>({
    url:
      `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}`
      + '/acceptance-evaluations',
    method: 'POST',
    silent: true,
  });
}

export function listDeviceAcceptanceEvidence(hardwareSn: string) {
  return request<DeviceAcceptanceEvidence[]>({
    url:
      `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}`
      + '/acceptance-evidence',
    method: 'GET',
    noStore: true,
  });
}

function controlPlatformDevice(
  hardwareSn: string,
  action: 'disablements' | 'restorations' | 'retirements',
  data: DeviceControlRequest,
  intent: CommandIntent,
) {
  return intent.execute<DeviceAsset, DeviceControlRequest>({
    url:
      `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}`
      + `/${action}`,
    method: 'POST',
    data,
  });
}

export function disablePlatformDevice(
  hardwareSn: string,
  data: DeviceControlRequest,
  intent: CommandIntent,
) {
  return controlPlatformDevice(hardwareSn, 'disablements', data, intent);
}

export function restorePlatformDevice(
  hardwareSn: string,
  data: DeviceControlRequest,
  intent: CommandIntent,
) {
  return controlPlatformDevice(hardwareSn, 'restorations', data, intent);
}

export function retirePlatformDevice(
  hardwareSn: string,
  data: DeviceControlRequest,
  intent: CommandIntent,
) {
  return controlPlatformDevice(hardwareSn, 'retirements', data, intent);
}

function platformDeviceAssetUrl(hardwareSn: string) {
  return `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}`;
}

export function listPlatformDeviceTechnicalIssues(hardwareSn: string) {
  return request<DeviceTechnicalIssue[]>({
    url: `${platformDeviceAssetUrl(hardwareSn)}/technical-issues`,
    method: 'GET',
    noStore: true,
  });
}

export function startPlatformBaselineMeasurementAttempt(
  hardwareSn: string,
  portNo: number,
  data: BaselineMeasurementAttemptRequest,
  intent: CommandIntent,
) {
  return intent.executeAccepted<
    BaselineMeasurementAccepted,
    BaselineMeasurementAttemptRequest
  >({
    url:
      `${platformDeviceAssetUrl(hardwareSn)}/ports/${portNo}`
      + '/baseline-measurement-attempts',
    method: 'POST',
    data,
  });
}

export function listPlatformDeviceConfigurationVersions(
  hardwareSn: string,
  params: { beforeVersionNo?: number; limit?: number } = {},
) {
  return request<DeviceConfigurationVersionPage>({
    url: `${platformDeviceAssetUrl(hardwareSn)}/configuration-versions`,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getPlatformDeviceConfigurationVersion(
  hardwareSn: string,
  versionNo: number,
) {
  return request<DeviceConfigurationVersion>({
    url:
      `${platformDeviceAssetUrl(hardwareSn)}/configuration-versions/`
      + versionNo,
    method: 'GET',
    noStore: true,
  });
}

export function rollForwardPlatformDeviceConfiguration(
  hardwareSn: string,
  data: DeviceConfigurationRollForwardRequest,
  intent: CommandIntent,
) {
  return intent.executeAccepted<
    DeviceConfigurationAccepted,
    DeviceConfigurationRollForwardRequest
  >({
    url: `${platformDeviceAssetUrl(hardwareSn)}/configuration-roll-forwards`,
    method: 'POST',
    data,
  });
}

export function getPlatformDeviceConfigurationApplication(
  hardwareSn: string,
  applicationUid: string,
) {
  return request<DeviceConfigurationApplication>({
    url:
      `${platformDeviceAssetUrl(hardwareSn)}/configuration-applications/`
      + encodeURIComponent(applicationUid),
    method: 'GET',
    noStore: true,
  });
}

export function resynchronizePlatformDeviceConfiguration(
  hardwareSn: string,
  applicationUid: string,
  data: DeviceConfigurationResynchronizationRequest,
  intent: CommandIntent,
) {
  return intent.executeAccepted<
    DeviceConfigurationAccepted,
    DeviceConfigurationResynchronizationRequest
  >({
    url:
      `${platformDeviceAssetUrl(hardwareSn)}/configuration-applications/`
      + `${encodeURIComponent(applicationUid)}/resynchronizations`,
    method: 'POST',
    data,
  });
}

export function listTenantDeviceAssets(
  params: TenantDeviceAssetListParams = {},
) {
  return request<DeviceAssetPage>({
    url: '/api/v1/web/device-assets',
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getTenantDeviceAsset(hardwareSn: string) {
  return request<DeviceAsset>({
    url: `/api/v1/web/device-assets/${encodeURIComponent(hardwareSn)}`,
    method: 'GET',
    noStore: true,
  });
}

export function getTenantDeviceRuntime(hardwareSn: string) {
  return request<DeviceRuntime>({
    url: `/api/v1/web/device-assets/${encodeURIComponent(hardwareSn)}/runtime`,
    method: 'GET',
    noStore: true,
  });
}

export function assignTenantDeviceOrganization(
  hardwareSn: string,
  data: AssignDeviceOrganizationRequest,
  intent: CommandIntent,
) {
  return intent.execute<DeviceAsset, AssignDeviceOrganizationRequest>({
    url:
      `/api/v1/web/device-assets/${encodeURIComponent(hardwareSn)}`
      + '/organization-assignments',
    method: 'POST',
    data,
  });
}

function organizationDeviceUrl(
  organizationCode: string,
  deviceCode?: string,
) {
  const base = `/api/v1/web/organizations/${encodeURIComponent(
    organizationCode,
  )}/devices`;
  return deviceCode ? `${base}/${encodeURIComponent(deviceCode)}` : base;
}

export function listOrganizationDevices(
  organizationCode: string,
  params: OrganizationDeviceListParams = {},
) {
  return request<DeviceAssetPage>({
    url: organizationDeviceUrl(organizationCode),
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getOrganizationDevice(
  organizationCode: string,
  deviceCode: string,
) {
  return request<DeviceAsset>({
    url: organizationDeviceUrl(organizationCode, deviceCode),
    method: 'GET',
    noStore: true,
  });
}

export function getOrganizationDeviceRuntime(
  organizationCode: string,
  deviceCode: string,
) {
  return request<DeviceRuntime>({
    url: `${organizationDeviceUrl(organizationCode, deviceCode)}/runtime`,
    method: 'GET',
    noStore: true,
  });
}

export function listDeviceConfigurationVersions(
  organizationCode: string,
  deviceCode: string,
  params: { beforeVersionNo?: number; limit?: number } = {},
) {
  return request<DeviceConfigurationVersionPage>({
    url:
      `${organizationDeviceUrl(organizationCode, deviceCode)}`
      + '/configuration-versions',
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getDeviceConfigurationVersion(
  organizationCode: string,
  deviceCode: string,
  versionNo: number,
) {
  return request<DeviceConfigurationVersion>({
    url:
      `${organizationDeviceUrl(organizationCode, deviceCode)}`
      + `/configuration-versions/${versionNo}`,
    method: 'GET',
    noStore: true,
  });
}

export function releaseDeviceConfiguration(
  organizationCode: string,
  deviceCode: string,
  data: DeviceConfigurationReleaseRequest,
  intent: CommandIntent,
) {
  return intent.executeAccepted<
    DeviceConfigurationAccepted,
    DeviceConfigurationReleaseRequest
  >({
    url:
      `${organizationDeviceUrl(organizationCode, deviceCode)}`
      + '/configuration-releases',
    method: 'POST',
    data,
  });
}

export function getDeviceConfigurationApplication(
  organizationCode: string,
  deviceCode: string,
  applicationUid: string,
) {
  return request<DeviceConfigurationApplication>({
    url:
      `${organizationDeviceUrl(organizationCode, deviceCode)}`
      + `/configuration-applications/${encodeURIComponent(applicationUid)}`,
    method: 'GET',
    noStore: true,
  });
}

export function resynchronizeDeviceConfiguration(
  organizationCode: string,
  deviceCode: string,
  applicationUid: string,
  data: DeviceConfigurationResynchronizationRequest,
  intent: CommandIntent,
) {
  return intent.executeAccepted<
    DeviceConfigurationAccepted,
    DeviceConfigurationResynchronizationRequest
  >({
    url:
      `${organizationDeviceUrl(organizationCode, deviceCode)}`
      + `/configuration-applications/${encodeURIComponent(applicationUid)}`
      + '/resynchronizations',
    method: 'POST',
    data,
  });
}
