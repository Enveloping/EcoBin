import type { CommandIntent } from './commandIntent';
import type { components, operations } from './generated/openapi';
import type { DirectoryContext } from './identityDirectory';
import request from './request';

type Schemas = components['schemas'];

export type CleanRecordItem = Schemas['CleanRecordItem'];
export type CleanRecordDetail = Schemas['WebCleanRecordDetail'];
export type CleanRecordCursorPage = Schemas['CleanRecordCursorPage'];
export type CleanRecordChange = Schemas['CleanRecordChange'];
export type CleanRecordChangeCursorPage =
  Schemas['CleanRecordChangeCursorPage'];
export type EditCleanRecordRequest = Schemas['EditCleanRecordRequest'];
export type EditCleanRecordResult = Schemas['EditCleanRecordResult'];
export type CleanRecordListParams = NonNullable<
  operations['listOrganizationCleanRecords']['parameters']['query']
>;

function collectionUrl(
  context: DirectoryContext,
  organizationCode: string,
): string {
  const organization = encodeURIComponent(organizationCode);
  if (context.domain === 'platform') {
    if (!context.tenantCode) {
      throw new Error('平台清运记录查询需要先选择目标租户');
    }
    return (
      `/api/v1/web/platform/tenants/${encodeURIComponent(context.tenantCode)}`
      + `/organizations/${organization}/clean-records`
    );
  }
  return `/api/v1/web/organizations/${organization}/clean-records`;
}

function recordUrl(
  context: DirectoryContext,
  organizationCode: string,
  cleanRecordNo: string,
): string {
  return (
    `${collectionUrl(context, organizationCode)}/`
    + encodeURIComponent(cleanRecordNo)
  );
}

export function listCleanRecords(
  context: DirectoryContext,
  organizationCode: string,
  params: CleanRecordListParams = {},
) {
  return request<CleanRecordCursorPage>({
    url: collectionUrl(context, organizationCode),
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getCleanRecord(
  context: DirectoryContext,
  organizationCode: string,
  cleanRecordNo: string,
) {
  return request<CleanRecordDetail>({
    url: recordUrl(context, organizationCode, cleanRecordNo),
    method: 'GET',
    noStore: true,
  });
}

export function listCleanRecordChanges(
  context: DirectoryContext,
  organizationCode: string,
  cleanRecordNo: string,
  params: { cursor?: string; limit?: number } = {},
) {
  return request<CleanRecordChangeCursorPage>({
    url: `${recordUrl(context, organizationCode, cleanRecordNo)}/changes`,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function editCleanRecord(
  context: DirectoryContext,
  organizationCode: string,
  cleanRecordNo: string,
  data: EditCleanRecordRequest,
  intent: CommandIntent,
) {
  return intent.execute<EditCleanRecordResult, EditCleanRecordRequest>({
    url: recordUrl(context, organizationCode, cleanRecordNo),
    method: 'PATCH',
    data,
  });
}
