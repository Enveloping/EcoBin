import type { components, operations } from './generated/openapi';
import type { DirectoryContext } from './identityDirectory';
import request from './request';

type Schemas = components['schemas'];

export type WalletSummary = Schemas['WalletSummary'];
export type WalletEntryType = Schemas['WalletEntryType'];
export type WalletEntrySourceType = Schemas['WalletEntrySourceType'];
export type PersonalWalletEntry = Schemas['PersonalWalletEntry'];
export type OrganizationWalletEntry = Schemas['OrganizationWalletEntry'];
export type PersonalWalletEntryCursorPage =
  Schemas['PersonalWalletEntryCursorPage'];
export type OrganizationWalletEntryCursorPage =
  Schemas['OrganizationWalletEntryCursorPage'];
export type PersonalWalletEntryListParams = NonNullable<
  operations['listWebOrganizationUserWalletEntries']['parameters']['query']
>;
export type OrganizationWalletEntryListParams = NonNullable<
  operations['listWebOrganizationWalletEntries']['parameters']['query']
>;

function organizationBase(
  context: DirectoryContext,
  organizationCode: string,
): string {
  const organization = encodeURIComponent(organizationCode);
  if (context.domain === 'platform') {
    if (!context.tenantCode) {
      throw new Error('平台钱包查询需要先选择目标租户');
    }
    return (
      `/api/v1/web/platform/tenants/${encodeURIComponent(context.tenantCode)}`
      + `/organizations/${organization}`
    );
  }
  return `/api/v1/web/organizations/${organization}`;
}

function organizationUserWalletUrl(
  context: DirectoryContext,
  organizationCode: string,
  organizationUserUid: string,
): string {
  return (
    `${organizationBase(context, organizationCode)}/organization-users/`
    + `${encodeURIComponent(organizationUserUid)}/wallet`
  );
}

export function getOrganizationUserWalletSummary(
  context: DirectoryContext,
  organizationCode: string,
  organizationUserUid: string,
) {
  return request<WalletSummary>({
    url: organizationUserWalletUrl(
      context,
      organizationCode,
      organizationUserUid,
    ),
    method: 'GET',
    noStore: true,
  });
}

export function listOrganizationUserWalletEntries(
  context: DirectoryContext,
  organizationCode: string,
  organizationUserUid: string,
  params: PersonalWalletEntryListParams = {},
) {
  return request<PersonalWalletEntryCursorPage>({
    url:
      `${organizationUserWalletUrl(
        context,
        organizationCode,
        organizationUserUid,
      )}/entries`,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function listOrganizationWalletEntries(
  context: DirectoryContext,
  organizationCode: string,
  params: OrganizationWalletEntryListParams = {},
) {
  return request<OrganizationWalletEntryCursorPage>({
    url: `${organizationBase(context, organizationCode)}/wallet-entries`,
    method: 'GET',
    params,
    noStore: true,
  });
}
