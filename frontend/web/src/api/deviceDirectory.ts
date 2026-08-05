import request from './request';
import type { CommandIntent } from './commandIntent';
import type { components, operations } from './generated/openapi';
import type { DirectoryContext } from './identityDirectory';

type Schemas = components['schemas'];

export type DeviceDeployment = Schemas['DeviceDeployment'];
export type DeviceAsset = Schemas['DeviceAsset'];
export type DeviceAssetLifecycleStatus =
  Schemas['DeviceAssetLifecycleStatus'];
export type CreateDeviceAssetRequest = Schemas['CreateDeviceAssetRequest'];
export type DeviceTenantAllocation = Schemas['DeviceTenantAllocation'];
export type DeviceTenantAllocationStatus =
  Schemas['DeviceTenantAllocationStatus'];
export type CreateDeviceTenantAllocationRequest =
  Schemas['CreateDeviceTenantAllocationRequest'];
export type CreateAllocatedDeviceDeploymentRequest =
  Schemas['CreateAllocatedDeviceDeploymentRequest'];
export type ReclaimDeviceTenantAllocationRequest =
  Schemas['ReclaimDeviceTenantAllocationRequest'];
export type ReturnDeviceDeploymentToTenantPoolRequest =
  Schemas['ReturnDeviceDeploymentToTenantPoolRequest'];
export type ConfirmOneNetCredentialRotationRequest =
  Schemas['ConfirmOneNetCredentialRotationRequest'];
export type ClearDeviceMaintenanceRequest =
  Schemas['ClearDeviceMaintenanceRequest'];
export type DeviceAcceptanceReadiness =
  Schemas['DeviceAcceptanceReadiness'];
export type DeviceDeploymentAcceptance =
  Schemas['DeviceDeploymentAcceptance'];
export type AcceptDeviceDeploymentRequest =
  Schemas['AcceptDeviceDeploymentRequest'];
export type OneNetCredentialRotationConfirmation =
  Schemas['OneNetCredentialRotationConfirmation'];
export type DeviceDeploymentLifecycleStatus =
  Schemas['DeviceDeploymentLifecycleStatus'];
export type DeviceDeploymentRuntime = Schemas['DeviceDeploymentRuntime'];
export type DevicePort = Schemas['DevicePort'];
export type DevicePortRuntime = Schemas['DevicePortRuntime'];
export type DeviceConfigurationApplicationStatus =
  Schemas['DeviceConfigurationApplicationStatus'];
export type DeviceConfigurationApplication =
  Schemas['DeviceConfigurationApplication'];
export type DeviceConfigurationAccepted =
  Schemas['DeviceConfigurationAccepted'];
export type DeviceConfigurationReleaseRequest =
  Schemas['DeviceConfigurationReleaseRequest'];
export type DeviceConfigurationVersion =
  Schemas['DeviceConfigurationVersion'];
export type DeviceConfigurationVersionSummary =
  Schemas['DeviceConfigurationVersionSummary'];
export type DeviceConfigurationVersionPage =
  Schemas['DeviceConfigurationVersionPage'];
export type DeviceDeploymentVersionCommand =
  Schemas['DeviceDeploymentVersionCommand'];
export type DeviceAssetListParams = NonNullable<
  operations['listPlatformDeviceAssets']['parameters']['query']
>;
export type DeviceDeploymentListParams = NonNullable<
  operations['listOrganizationDeviceDeployments']['parameters']['query']
>;
export type TenantDeviceAllocationListParams = NonNullable<
  operations['listTenantDeviceAssetAllocations']['parameters']['query']
>;
export type PlatformDeviceAllocationListParams = NonNullable<
  operations['listPlatformDeviceAssetAllocations']['parameters']['query']
>;

function deploymentCollectionUrl(
  context: DirectoryContext,
  organizationCode: string,
): string {
  const organization = encodeURIComponent(organizationCode);
  if (context.domain === 'platform') {
    if (!context.tenantCode) {
      throw new Error('平台设备查询需要先选择目标租户');
    }
    return (
      `/api/v1/web/platform/tenants/${encodeURIComponent(context.tenantCode)}`
      + `/organizations/${organization}/device-deployments`
    );
  }
  return `/api/v1/web/organizations/${organization}/device-deployments`;
}

function deploymentUrl(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
): string {
  return `${deploymentCollectionUrl(context, organizationCode)}/${encodeURIComponent(
    deploymentCode,
  )}`;
}

function platformDeploymentUrl(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
): string {
  if (context.domain !== 'platform' || !context.tenantCode) {
    throw new Error('该操作需要平台管理员和显式租户范围');
  }
  return deploymentUrl(
    context,
    organizationCode,
    deploymentCode,
  );
}

export function listPlatformDeviceAssets(
  params: DeviceAssetListParams = {},
) {
  return request<Schemas['DeviceAssetPage']>({
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

export function listTenantDeviceAssetAllocations(
  params: TenantDeviceAllocationListParams = {},
) {
  return request<Schemas['DeviceTenantAllocationPage']>({
    url: '/api/v1/web/device-asset-allocations',
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getTenantDeviceAssetAllocation(allocationUid: string) {
  return request<DeviceTenantAllocation>({
    url: `/api/v1/web/device-asset-allocations/${encodeURIComponent(allocationUid)}`,
    method: 'GET',
    noStore: true,
  });
}

export function listPlatformDeviceAssetAllocations(
  params: PlatformDeviceAllocationListParams = {},
) {
  return request<Schemas['DeviceTenantAllocationPage']>({
    url: '/api/v1/web/platform/device-asset-allocations',
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getPlatformDeviceAssetAllocation(allocationUid: string) {
  return request<DeviceTenantAllocation>({
    url:
      '/api/v1/web/platform/device-asset-allocations/'
      + encodeURIComponent(allocationUid),
    method: 'GET',
    noStore: true,
  });
}

export function allocatePlatformDeviceAssetToTenant(
  tenantCode: string,
  data: CreateDeviceTenantAllocationRequest,
  intent: CommandIntent,
) {
  return intent.execute<
    DeviceTenantAllocation,
    CreateDeviceTenantAllocationRequest
  >({
    url:
      `/api/v1/web/platform/tenants/${encodeURIComponent(tenantCode)}`
      + '/device-asset-allocations',
    method: 'POST',
    data,
  });
}

export function reclaimPlatformDeviceAssetAllocation(
  allocationUid: string,
  data: ReclaimDeviceTenantAllocationRequest,
  intent: CommandIntent,
) {
  return intent.execute<
    DeviceTenantAllocation,
    ReclaimDeviceTenantAllocationRequest
  >({
    url:
      '/api/v1/web/platform/device-asset-allocations/'
      + `${encodeURIComponent(allocationUid)}/reclaims`,
    method: 'POST',
    data,
  });
}

export function confirmPlatformOneNetCredentialRotation(
  hardwareSn: string,
  data: ConfirmOneNetCredentialRotationRequest,
  intent: CommandIntent,
) {
  return intent.execute<
    OneNetCredentialRotationConfirmation,
    ConfirmOneNetCredentialRotationRequest
  >({
    url:
      `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}`
      + '/onenet-credential-rotation-confirmations',
    method: 'POST',
    data,
  });
}

export function clearPlatformDeviceMaintenanceIsolation(
  hardwareSn: string,
  data: ClearDeviceMaintenanceRequest,
  intent: CommandIntent,
) {
  return intent.execute<DeviceTenantAllocation, ClearDeviceMaintenanceRequest>({
    url:
      `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}`
      + '/maintenance-clearances',
    method: 'POST',
    data,
  });
}

export function createOrganizationDeviceDeploymentFromTenantPool(
  context: DirectoryContext,
  organizationCode: string,
  data: CreateAllocatedDeviceDeploymentRequest,
  intent: CommandIntent,
) {
  if (context.domain === 'platform') {
    throw new Error('平台管理员不能代替租户选择部署机构');
  }
  return intent.execute<
    DeviceDeployment,
    CreateAllocatedDeviceDeploymentRequest
  >({
    url: deploymentCollectionUrl(context, organizationCode),
    method: 'POST',
    data,
  });
}

export function listDeviceDeployments(
  context: DirectoryContext,
  organizationCode: string,
  params: DeviceDeploymentListParams = {},
) {
  return request<Schemas['DeviceDeploymentPage']>({
    url: deploymentCollectionUrl(context, organizationCode),
    method: 'GET',
    params,
  });
}

export function getDeviceDeployment(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
) {
  return request<DeviceDeployment>({
    url: deploymentUrl(context, organizationCode, deploymentCode),
    method: 'GET',
    noStore: true,
  });
}

export function listDeviceDeploymentPorts(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
) {
  return request<DevicePort[]>({
    url: `${deploymentUrl(context, organizationCode, deploymentCode)}/ports`,
    method: 'GET',
    noStore: true,
  });
}

export function getDeviceDeploymentRuntime(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
) {
  return request<DeviceDeploymentRuntime>({
    url: `${deploymentUrl(context, organizationCode, deploymentCode)}/runtime`,
    method: 'GET',
    noStore: true,
  });
}

export function getDevicePortRuntime(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  portNo: number,
) {
  return request<DevicePortRuntime>({
    url:
      `${deploymentUrl(context, organizationCode, deploymentCode)}`
      + `/ports/${portNo}/runtime`,
    method: 'GET',
    noStore: true,
  });
}

function executeDeploymentCommand<T extends object>(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  suffix: string,
  data: T,
  intent: CommandIntent,
) {
  return intent.execute<DeviceDeployment, T>({
    url: `${deploymentUrl(context, organizationCode, deploymentCode)}/${suffix}`,
    method: 'POST',
    data,
  });
}

export function setDeviceBusinessEnabled(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  enabled: boolean,
  data: DeviceDeploymentVersionCommand,
  intent: CommandIntent,
) {
  if (context.domain === 'platform') {
    throw new Error('平台管理员不能代替租户开启或关闭经营');
  }
  return executeDeploymentCommand(
    context,
    organizationCode,
    deploymentCode,
    `business-switch/${enabled ? 'enablements' : 'disablements'}`,
    data,
    intent,
  );
}

export function returnDeviceDeploymentToTenantPool(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  data: ReturnDeviceDeploymentToTenantPoolRequest,
  intent: CommandIntent,
) {
  if (context.domain === 'platform') {
    throw new Error('平台管理员不能代替租户发起机构调拨');
  }
  return intent.execute<
    DeviceTenantAllocation,
    ReturnDeviceDeploymentToTenantPoolRequest
  >({
    url:
      `${deploymentUrl(context, organizationCode, deploymentCode)}`
      + '/returns-to-tenant-pool',
    method: 'POST',
    data,
  });
}

export function getPlatformDeviceAcceptanceReadiness(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
) {
  return request<DeviceAcceptanceReadiness>({
    url:
      `${platformDeploymentUrl(context, organizationCode, deploymentCode)}`
      + '/acceptance-readiness',
    method: 'GET',
    noStore: true,
  });
}

export function listPlatformDeviceDeploymentAcceptances(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
) {
  return request<DeviceDeploymentAcceptance[]>({
    url:
      `${platformDeploymentUrl(context, organizationCode, deploymentCode)}`
      + '/acceptances',
    method: 'GET',
    noStore: true,
  });
}

export function acceptPlatformDeviceDeployment(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  data: AcceptDeviceDeploymentRequest,
  intent: CommandIntent,
) {
  return intent.execute<
    DeviceDeploymentAcceptance,
    AcceptDeviceDeploymentRequest
  >({
    url:
      `${platformDeploymentUrl(context, organizationCode, deploymentCode)}`
      + '/acceptances',
    method: 'POST',
    data,
  });
}

export function suspendPlatformDeviceDeploymentTechnically(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  data: DeviceDeploymentVersionCommand,
  intent: CommandIntent,
) {
  return intent.execute<DeviceDeployment, DeviceDeploymentVersionCommand>({
    url:
      `${platformDeploymentUrl(context, organizationCode, deploymentCode)}`
      + '/technical-suspensions',
    method: 'POST',
    data,
  });
}

export function listDeviceConfigurationVersions(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  params: { beforeVersionNo?: number; limit?: number } = {},
) {
  return request<DeviceConfigurationVersionPage>({
    url:
      `${deploymentUrl(context, organizationCode, deploymentCode)}`
      + '/configuration-versions',
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getDeviceConfigurationVersion(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  versionNo: number,
) {
  return request<DeviceConfigurationVersion>({
    url:
      `${deploymentUrl(context, organizationCode, deploymentCode)}`
      + `/configuration-versions/${versionNo}`,
    method: 'GET',
    noStore: true,
  });
}

export function releaseDeviceConfiguration(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  data: DeviceConfigurationReleaseRequest,
  intent: CommandIntent,
) {
  return intent.executeAccepted<
    DeviceConfigurationAccepted,
    DeviceConfigurationReleaseRequest
  >({
    url:
      `${deploymentUrl(context, organizationCode, deploymentCode)}`
      + '/configuration-releases',
    method: 'POST',
    data,
  });
}

export function getDeviceConfigurationApplication(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  applicationUid: string,
) {
  return request<DeviceConfigurationApplication>({
    url:
      `${deploymentUrl(context, organizationCode, deploymentCode)}`
      + `/configuration-applications/${encodeURIComponent(applicationUid)}`,
    method: 'GET',
    noStore: true,
  });
}

export function resynchronizeDeviceConfiguration(
  context: DirectoryContext,
  organizationCode: string,
  deploymentCode: string,
  applicationUid: string,
  data: DeviceDeploymentVersionCommand,
  intent: CommandIntent,
) {
  return intent.executeAccepted<
    DeviceConfigurationAccepted,
    DeviceDeploymentVersionCommand
  >({
    url:
      `${deploymentUrl(context, organizationCode, deploymentCode)}`
      + `/configuration-applications/${encodeURIComponent(applicationUid)}`
      + '/resynchronizations',
    method: 'POST',
    data,
  });
}
