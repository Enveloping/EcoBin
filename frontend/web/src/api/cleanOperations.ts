import type { components, operations } from './generated/openapi';
import type { DirectoryContext } from './identityDirectory';
import request from './request';

type Schemas = components['schemas'];

export type CleanOperationItem = Schemas['WebCleanOperationItem'];
export type CleanOperationDetail = Schemas['WebCleanOperationDetail'];
export type CleanOperationCursorPage = Schemas['CleanOperationCursorPage'];
export type CleanOperationStatus = Schemas['CleanOperationStatus'];
export type CleanOperationListParams = NonNullable<
  operations['listOrganizationCleanOperations']['parameters']['query']
>;

function collectionUrl(
  context: DirectoryContext,
  organizationCode: string,
): string {
  const organization = encodeURIComponent(organizationCode);
  if (context.domain === 'platform') {
    if (!context.tenantCode) {
      throw new Error('平台清运操作查询需要先选择目标租户');
    }
    return (
      `/api/v1/web/platform/tenants/${encodeURIComponent(context.tenantCode)}`
      + `/organizations/${organization}/clean-operations`
    );
  }
  return `/api/v1/web/organizations/${organization}/clean-operations`;
}

export function listCleanOperations(
  context: DirectoryContext,
  organizationCode: string,
  params: CleanOperationListParams = {},
) {
  return request<CleanOperationCursorPage>({
    url: collectionUrl(context, organizationCode),
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getCleanOperation(
  context: DirectoryContext,
  organizationCode: string,
  operationUid: string,
) {
  return request<CleanOperationDetail>({
    url:
      `${collectionUrl(context, organizationCode)}/`
      + encodeURIComponent(operationUid),
    method: 'GET',
    noStore: true,
  });
}
