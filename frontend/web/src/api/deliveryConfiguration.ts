import type { CommandIntent } from './commandIntent';
import type { components } from './generated/openapi';
import type { DirectoryContext } from './identityDirectory';
import request from './request';

type Schemas = components['schemas'];

export type DeliveryConfigurationVersion =
  Schemas['DeliveryConfigurationVersion'];
export type DeliveryConfigurationVersionPage =
  Schemas['DeliveryConfigurationVersionPage'];
export type DeliveryConfigurationReleaseRequest =
  Schemas['DeliveryConfigurationReleaseRequest'];

function organizationBase(
  context: DirectoryContext,
  organizationCode: string,
): string {
  const organization = encodeURIComponent(organizationCode);
  if (context.domain === 'platform') {
    if (!context.tenantCode) {
      throw new Error('平台投递规则管理需要先选择目标租户');
    }
    return (
      `/api/v1/web/platform/tenants/${encodeURIComponent(context.tenantCode)}`
      + `/organizations/${organization}`
    );
  }
  return `/api/v1/web/organizations/${organization}`;
}

export function getDeliveryConfiguration(
  context: DirectoryContext,
  organizationCode: string,
) {
  return request<DeliveryConfigurationVersion>({
    url: `${organizationBase(context, organizationCode)}/delivery-configuration`,
    method: 'GET',
    noStore: true,
  });
}

export function listDeliveryConfigurationVersions(
  context: DirectoryContext,
  organizationCode: string,
  params: { beforeVersionNo?: number; limit?: number } = {},
) {
  return request<DeliveryConfigurationVersionPage>({
    url:
      `${organizationBase(context, organizationCode)}`
      + '/delivery-configuration-versions',
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getDeliveryConfigurationVersion(
  context: DirectoryContext,
  organizationCode: string,
  versionNo: number,
) {
  return request<DeliveryConfigurationVersion>({
    url:
      `${organizationBase(context, organizationCode)}`
      + `/delivery-configuration-versions/${versionNo}`,
    method: 'GET',
    noStore: true,
  });
}

export function releaseDeliveryConfiguration(
  context: DirectoryContext,
  organizationCode: string,
  data: DeliveryConfigurationReleaseRequest,
  intent: CommandIntent,
) {
  return intent.execute<
    DeliveryConfigurationVersion,
    DeliveryConfigurationReleaseRequest
  >({
    url:
      `${organizationBase(context, organizationCode)}`
      + '/delivery-configuration-releases',
    method: 'POST',
    data,
  });
}
