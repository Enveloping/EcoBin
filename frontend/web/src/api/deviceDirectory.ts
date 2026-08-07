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
export type DeviceConfigurationVersion =
  Schemas['DeviceConfigurationVersion'];
export type DeviceConfigurationVersionSummary =
  Schemas['DeviceConfigurationVersionSummary'];
export type DeviceConfigurationVersionPage =
  Schemas['DeviceConfigurationVersionPage'];

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
