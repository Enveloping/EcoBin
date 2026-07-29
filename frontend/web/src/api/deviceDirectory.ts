import request from './request';
import type { components, operations } from './generated/openapi';
import type { DirectoryContext } from './identityDirectory';

type Schemas = components['schemas'];

export type DeviceDeployment = Schemas['DeviceDeployment'];
export type DeviceDeploymentLifecycleStatus =
  Schemas['DeviceDeploymentLifecycleStatus'];
export type DeviceConfigurationApplicationStatus =
  Schemas['DeviceConfigurationApplicationStatus'];
export type DeviceDeploymentListParams = NonNullable<
  operations['listOrganizationDeviceDeployments']['parameters']['query']
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
