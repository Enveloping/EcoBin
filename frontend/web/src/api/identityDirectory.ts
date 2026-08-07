import request from './request';
import type { CommandIntent } from './commandIntent';
import type { components } from './generated/openapi';
import type {
  EffectiveAccess,
  IdentityOrganization,
  IdentityTenant,
  OrganizationUser,
  PageData,
  PermissionDefinition,
  StaffAccount,
  StaffMembership,
  WebLoginDomain,
} from '@/types';

type Schemas = components['schemas'];

export type BindingSnapshot = Schemas['BindingSnapshot'];
export type OrganizationUserLookup = Schemas['OrganizationUserLookup'];
export type StaffMiniappBindingLookup = Schemas['StaffMiniappBindingLookup'];
export type StaffMiniappBinding = Schemas['StaffMiniappBinding'];
export type MiniappConfiguration = Schemas['MiniappConfiguration'];
export type MiniappConfigurationMutation =
  Schemas['MiniappConfigurationMutation'];
export type PutMiniappConfigurationRequest =
  Schemas['PutMiniappConfigurationRequest'];

export interface DirectoryContext {
  domain: WebLoginDomain;
  tenantCode?: string;
}

export interface DirectoryPageParams {
  page?: number;
  pageSize?: number;
  status?: string;
  query?: string;
}

type DirectoryFilterParams = Omit<
  DirectoryPageParams,
  'page' | 'pageSize'
>;

export interface OrganizationUserPageParams {
  page?: number;
  pageSize?: number;
  status?: OrganizationUser['status'];
  phoneBound?: boolean;
  registeredFrom?: string;
  registeredTo?: string;
  sourceDeviceCode?: string;
  cleanOperation?: boolean;
}

export interface TenantProfileInput {
  enterpriseName: string;
  contactName?: string;
  contactPhone?: string;
  contactAddress?: string;
  expectedVersion: number;
}

export interface OrganizationProfileInput {
  organizationName: string;
  contactPhone?: string;
  contactAddress?: string;
  expectedVersion: number;
}

export interface StaffProfileInput {
  displayName: string;
  contactPhone?: string;
  expectedVersion: number;
}

function scopedBase(context: DirectoryContext): string {
  if (context.domain === 'platform') {
    if (!context.tenantCode) {
      throw new Error('平台目录操作需要先选择目标租户');
    }
    return `/api/v1/web/platform/tenants/${encodeURIComponent(context.tenantCode)}`;
  }
  return '/api/v1/web';
}

function write<T>(
  intent: CommandIntent,
  url: string,
  method: 'POST' | 'PUT',
  data: unknown,
) {
  return intent.execute<T>({
    url,
    method,
    data,
  });
}

const DIRECTORY_OPTION_PAGE_SIZE = 200;

async function collectDirectoryItems<T>(
  loadPage: (page: number) => Promise<PageData<T>>,
): Promise<T[]> {
  const items: T[] = [];
  let page = 1;
  while (true) {
    const result = await loadPage(page);
    items.push(...result.items);
    if (items.length >= result.total || result.items.length === 0) {
      return items;
    }
    page += 1;
  }
}

export function listIdentityTenants(params: DirectoryPageParams = {}) {
  return request<PageData<IdentityTenant>>({
    url: '/api/v1/web/platform/tenants',
    method: 'GET',
    params,
  });
}

export function listAllIdentityTenants(
  params: DirectoryFilterParams = {},
) {
  return collectDirectoryItems((page) =>
    listIdentityTenants({
      ...params,
      page,
      pageSize: DIRECTORY_OPTION_PAGE_SIZE,
    }));
}

export function getIdentityTenant(tenantCode: string) {
  return request<IdentityTenant>({
    url: `/api/v1/web/platform/tenants/${encodeURIComponent(tenantCode)}`,
    method: 'GET',
  });
}

export function createIdentityTenant(
  data: {
    tenantCode: string;
    enterpriseName: string;
    contactName?: string;
    contactPhone?: string;
    contactAddress?: string;
  },
  intent: CommandIntent,
) {
  return write<IdentityTenant>(
    intent,
    '/api/v1/web/platform/tenants',
    'POST',
    data,
  );
}

export function updateIdentityTenant(
  tenantCode: string,
  data: TenantProfileInput,
  intent: CommandIntent,
) {
  return write<IdentityTenant>(
    intent,
    `/api/v1/web/platform/tenants/${encodeURIComponent(tenantCode)}/profile`,
    'PUT',
    data,
  );
}

export function createTenantPrincipal(
  tenantCode: string,
  data: {
    loginName: string;
    initialPassword: string;
    displayName: string;
    contactPhone?: string;
    expectedVersion: number;
  },
  intent: CommandIntent,
) {
  return write<StaffAccount>(
    intent,
    `/api/v1/web/platform/tenants/${encodeURIComponent(tenantCode)}/principal-account`,
    'POST',
    data,
  );
}

export function resetTenantPrincipalPassword(
  tenantCode: string,
  principal: NonNullable<IdentityTenant['principalAccount']>,
  newPassword: string,
  intent: CommandIntent,
) {
  return write<StaffAccount>(
    intent,
    `/api/v1/web/platform/tenants/${encodeURIComponent(tenantCode)}/principal-account/password-resets`,
    'POST',
    {
      newPassword,
      expectedVersion: principal.version,
      expectedAuthVersion: principal.authVersion,
    },
  );
}

export function changeTenantStatus(
  tenantCode: string,
  enabled: boolean,
  expectedVersion: number,
  intent: CommandIntent,
  reason?: string,
) {
  return write<IdentityTenant>(
    intent,
    `/api/v1/web/platform/tenants/${encodeURIComponent(tenantCode)}/${
      enabled ? 'activations' : 'deactivations'
    }`,
    'POST',
    { expectedVersion, reason },
  );
}

export function getCurrentTenant() {
  return request<IdentityTenant>({
    url: '/api/v1/web/tenants/current',
    method: 'GET',
  });
}

export function updateCurrentTenant(
  data: TenantProfileInput,
  intent: CommandIntent,
) {
  return write<IdentityTenant>(
    intent,
    '/api/v1/web/tenants/current/profile',
    'PUT',
    data,
  );
}

export function listOrganizations(
  context: DirectoryContext,
  params: DirectoryPageParams = {},
) {
  return request<PageData<IdentityOrganization>>({
    url: `${scopedBase(context)}/organizations`,
    method: 'GET',
    params,
  });
}

export function listAllOrganizations(
  context: DirectoryContext,
  params: DirectoryFilterParams = {},
) {
  return collectDirectoryItems((page) =>
    listOrganizations(context, {
      ...params,
      page,
      pageSize: DIRECTORY_OPTION_PAGE_SIZE,
    }));
}

export function createOrganization(
  context: DirectoryContext,
  data: {
    organizationCode: string;
    organizationName: string;
    contactPhone?: string;
    contactAddress?: string;
  },
  intent: CommandIntent,
) {
  return write<IdentityOrganization>(
    intent,
    `${scopedBase(context)}/organizations`,
    'POST',
    data,
  );
}

export function updateOrganization(
  context: DirectoryContext,
  organizationCode: string,
  data: OrganizationProfileInput,
  intent: CommandIntent,
) {
  return write<IdentityOrganization>(
    intent,
    `${scopedBase(context)}/organizations/${encodeURIComponent(organizationCode)}/profile`,
    'PUT',
    data,
  );
}

export function changeOrganizationStatus(
  context: DirectoryContext,
  organization: IdentityOrganization,
  enabled: boolean,
  intent: CommandIntent,
  reason?: string,
) {
  return write<IdentityOrganization>(
    intent,
    `${scopedBase(context)}/organizations/${encodeURIComponent(
      organization.organizationCode,
    )}/${enabled ? 'activations' : 'deactivations'}`,
    'POST',
    { expectedVersion: organization.version, reason },
  );
}

function organizationMiniappBase(
  context: DirectoryContext,
  organizationCode: string,
): string {
  return `${scopedBase(context)}/organizations/${encodeURIComponent(
    organizationCode,
  )}`;
}

export function getOrganizationMiniappConfiguration(
  context: DirectoryContext,
  organizationCode: string,
) {
  return request<MiniappConfiguration>({
    url: `${organizationMiniappBase(
      context,
      organizationCode,
    )}/miniapp-configuration`,
    method: 'GET',
    noStore: true,
    silent: true,
  });
}

export function putOrganizationMiniappConfiguration(
  context: DirectoryContext,
  organizationCode: string,
  data: PutMiniappConfigurationRequest,
  intent: CommandIntent,
) {
  return write<MiniappConfigurationMutation>(
    intent,
    `${organizationMiniappBase(
      context,
      organizationCode,
    )}/miniapp-configuration`,
    'PUT',
    data,
  );
}

export function activateOrganizationMiniappConfiguration(
  context: DirectoryContext,
  organizationCode: string,
  expectedVersion: number,
  intent: CommandIntent,
  reason?: string,
) {
  return write<MiniappConfigurationMutation>(
    intent,
    `${organizationMiniappBase(
      context,
      organizationCode,
    )}/miniapp-configuration/activations`,
    'POST',
    { expectedVersion, reason },
  );
}

export function changeOrganizationMiniappLogin(
  context: DirectoryContext,
  organizationCode: string,
  enabled: boolean,
  expectedVersion: number,
  intent: CommandIntent,
  reason?: string,
) {
  return write<MiniappConfigurationMutation>(
    intent,
    `${organizationMiniappBase(
      context,
      organizationCode,
    )}/miniapp-login/${enabled ? 'enablements' : 'disablements'}`,
    'POST',
    { expectedVersion, reason },
  );
}

export function listStaffAccounts(
  context: DirectoryContext,
  params: DirectoryPageParams = {},
) {
  return request<PageData<StaffAccount>>({
    url: `${scopedBase(context)}/staff-accounts`,
    method: 'GET',
    params,
  });
}

export function listAllStaffAccounts(
  context: DirectoryContext,
  params: DirectoryFilterParams = {},
) {
  return collectDirectoryItems((page) =>
    listStaffAccounts(context, {
      ...params,
      page,
      pageSize: DIRECTORY_OPTION_PAGE_SIZE,
    }));
}

export function getStaffAccount(
  context: DirectoryContext,
  staffUid: string,
) {
  return request<StaffAccount>({
    url: `${scopedBase(context)}/staff-accounts/${encodeURIComponent(
      staffUid,
    )}`,
    method: 'GET',
  });
}

export function createStaffAccount(
  context: DirectoryContext,
  data: {
    loginName: string;
    initialPassword: string;
    displayName: string;
    contactPhone?: string;
    permissionCodes: string[];
  },
  intent: CommandIntent,
) {
  return write<StaffAccount>(
    intent,
    `${scopedBase(context)}/staff-accounts`,
    'POST',
    data,
  );
}

export function updateStaffAccount(
  context: DirectoryContext,
  staffUid: string,
  data: StaffProfileInput,
  intent: CommandIntent,
) {
  return write<StaffAccount>(
    intent,
    `${scopedBase(context)}/staff-accounts/${encodeURIComponent(staffUid)}/profile`,
    'PUT',
    data,
  );
}

export function changeStaffStatus(
  context: DirectoryContext,
  staff: StaffAccount,
  enabled: boolean,
  intent: CommandIntent,
  reason?: string,
) {
  return write<StaffAccount>(
    intent,
    `${scopedBase(context)}/staff-accounts/${encodeURIComponent(
      staff.staffAccountUid,
    )}/${enabled ? 'activations' : 'deactivations'}`,
    'POST',
    {
      expectedVersion: staff.version,
      expectedAuthVersion: staff.authVersion,
      reason,
    },
  );
}

export function resetStaffPassword(
  context: DirectoryContext,
  staff: StaffAccount,
  newPassword: string,
  intent: CommandIntent,
) {
  return write<StaffAccount>(
    intent,
    `${scopedBase(context)}/staff-accounts/${encodeURIComponent(
      staff.staffAccountUid,
    )}/password-resets`,
    'POST',
    {
      newPassword,
      expectedVersion: staff.version,
      expectedAuthVersion: staff.authVersion,
    },
  );
}

export function updateOwnProfile(
  data: StaffProfileInput,
  intent: CommandIntent,
) {
  return write<StaffAccount>(
    intent,
    '/api/v1/web/staff-accounts/current/profile',
    'PUT',
    data,
  );
}

export function changeOwnPassword(
  data: {
    currentPassword: string;
    newPassword: string;
    expectedVersion: number;
    expectedAuthVersion: number;
  },
  intent: CommandIntent,
) {
  return write<StaffAccount>(
    intent,
    '/api/v1/web/staff-accounts/current/password-changes',
    'POST',
    data,
  );
}

export function listPermissionDefinitions(context: DirectoryContext) {
  const url = context.domain === 'platform'
    ? '/api/v1/web/platform/permission-definitions'
    : '/api/v1/web/permission-definitions';
  return request<PermissionDefinition[]>({ url, method: 'GET' });
}

export function getEffectiveAccess(
  context: DirectoryContext,
  staffUid: string,
) {
  return request<EffectiveAccess>({
    url: `${scopedBase(context)}/staff-accounts/${encodeURIComponent(staffUid)}/effective-access`,
    method: 'GET',
  });
}

export function getCurrentEffectiveAccess() {
  return request<EffectiveAccess>({
    url: '/api/v1/web/staff-accounts/current/effective-access',
    method: 'GET',
  });
}

export function replaceTenantPermissions(
  context: DirectoryContext,
  staffUid: string,
  permissionCodes: string[],
  expectedAuthVersion: number,
  intent: CommandIntent,
) {
  return write<EffectiveAccess>(
    intent,
    `${scopedBase(context)}/staff-accounts/${encodeURIComponent(staffUid)}/tenant-permissions`,
    'PUT',
    { permissionCodes, expectedAuthVersion },
  );
}

export function listMemberships(
  context: DirectoryContext,
  organizationCode: string,
  page = 1,
  pageSize = 100,
) {
  return request<PageData<StaffMembership>>({
    url: `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/staff-memberships`,
    method: 'GET',
    params: { page, pageSize },
  });
}

export function createMembership(
  context: DirectoryContext,
  organizationCode: string,
  data: {
    staffAccountUid: string;
    manager: boolean;
    permissionCodes: string[];
    expectedAuthVersion: number;
  },
  intent: CommandIntent,
) {
  return write<StaffMembership>(
    intent,
    `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/staff-memberships`,
    'POST',
    data,
  );
}

export function provisionOrganizationStaff(
  context: DirectoryContext,
  organizationCode: string,
  data: {
    loginName: string;
    initialPassword: string;
    displayName: string;
    contactPhone?: string;
    manager: boolean;
    permissionCodes: string[];
  },
  intent: CommandIntent,
) {
  return write<{
    staffAccount: StaffAccount;
    membership: StaffMembership;
  }>(
    intent,
    `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/staff-account-provisionings`,
    'POST',
    data,
  );
}

export function replaceMembershipAuthorization(
  context: DirectoryContext,
  membership: StaffMembership,
  manager: boolean,
  permissionCodes: string[],
  intent: CommandIntent,
) {
  return write<StaffMembership>(
    intent,
    `${scopedBase(context)}/organizations/${encodeURIComponent(
      membership.organizationCode,
    )}/staff-memberships/${encodeURIComponent(
      membership.staffAccountUid,
    )}/authorization`,
    'PUT',
    {
      manager,
      permissionCodes,
      expectedVersion: membership.version,
      expectedAuthVersion: membership.authVersion,
    },
  );
}

export function changeMembershipStatus(
  context: DirectoryContext,
  membership: StaffMembership,
  enabled: boolean,
  intent: CommandIntent,
  manager = false,
  permissionCodes: string[] = [],
  reason?: string,
) {
  return write<StaffMembership>(
    intent,
    `${scopedBase(context)}/organizations/${encodeURIComponent(
      membership.organizationCode,
    )}/staff-memberships/${encodeURIComponent(
      membership.staffAccountUid,
    )}/${enabled ? 'activations' : 'deactivations'}`,
    'POST',
    enabled
      ? {
          manager,
          permissionCodes,
          expectedVersion: membership.version,
          expectedAuthVersion: membership.authVersion,
          reason,
        }
      : {
          expectedVersion: membership.version,
          expectedAuthVersion: membership.authVersion,
          reason,
        },
  );
}

export function listOrganizationUsers(
  context: DirectoryContext,
  organizationCode: string,
  params: OrganizationUserPageParams = {},
) {
  return request<PageData<OrganizationUser>>({
    url: `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/organization-users`,
    method: 'GET',
    params,
  });
}

export function getOrganizationUser(
  context: DirectoryContext,
  organizationCode: string,
  organizationUserUid: string,
) {
  return request<OrganizationUser>({
    url: `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/organization-users/${encodeURIComponent(organizationUserUid)}`,
    method: 'GET',
  });
}

export function changeOrganizationUserStatus(
  context: DirectoryContext,
  organizationCode: string,
  user: OrganizationUser,
  enabled: boolean,
  intent: CommandIntent,
  reason?: string,
) {
  return write<OrganizationUser>(
    intent,
    `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/organization-users/${encodeURIComponent(
      user.organizationUserUid,
    )}/${enabled ? 'restorations' : 'freezes'}`,
    'POST',
    {
      expectedVersion: user.version,
      expectedAuthVersion: user.authVersion,
      reason,
    },
  );
}

export function changeOrganizationUserCleanOperation(
  context: DirectoryContext,
  organizationCode: string,
  user: OrganizationUser,
  enabled: boolean,
  intent: CommandIntent,
  reason?: string,
) {
  return write<OrganizationUser>(
    intent,
    `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/organization-users/${encodeURIComponent(
      user.organizationUserUid,
    )}/capabilities/clean-operation/${enabled ? 'grants' : 'revocations'}`,
    'POST',
    {
      expectedVersion: user.version,
      expectedAuthVersion: user.authVersion,
      reason,
    },
  );
}

export function lookupOrganizationUserByPhone(
  context: DirectoryContext,
  organizationCode: string,
  phoneNumber: string,
  options: { silent?: boolean } = {},
) {
  const url = context.domain === 'platform'
    ? `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/organization-users/phone-lookups`
    : `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/organization-user-lookups`;
  return request<OrganizationUserLookup>({
    url,
    method: 'POST',
    data: { phoneNumber },
    silent: options.silent,
  });
}

export function getStaffMiniappBinding(
  context: DirectoryContext,
  organizationCode: string,
  staffUid: string,
) {
  return request<StaffMiniappBindingLookup>({
    url: `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/staff-accounts/${encodeURIComponent(staffUid)}/miniapp-binding`,
    method: 'GET',
  });
}

export function setStaffMiniappBinding(
  context: DirectoryContext,
  organizationCode: string,
  staffUid: string,
  data: {
    organizationUserUid: string;
    expectedStaffBinding: BindingSnapshot | null;
    expectedOrganizationUserBinding: BindingSnapshot | null;
    reason?: string;
  },
  intent: CommandIntent,
) {
  return write<StaffMiniappBinding>(
    intent,
    `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/staff-accounts/${encodeURIComponent(staffUid)}/miniapp-binding`,
    'PUT',
    data,
  );
}

export function revokeStaffMiniappBinding(
  context: DirectoryContext,
  organizationCode: string,
  binding: Pick<StaffMiniappBinding, 'bindingUid' | 'version'>,
  intent: CommandIntent,
  reason?: string,
) {
  const url = context.domain === 'platform'
    ? `${scopedBase(context)}/organizations/${encodeURIComponent(
      organizationCode,
    )}/staff-miniapp-bindings/${encodeURIComponent(binding.bindingUid)}/revocations`
    : `/api/v1/web/staff-miniapp-bindings/${encodeURIComponent(
      binding.bindingUid,
    )}/revocations`;
  return write<StaffMiniappBinding>(intent, url, 'POST', {
    expectedVersion: binding.version,
    reason,
  });
}
