import request from './request';
import type {
  EffectiveAccess,
  IdentityOrganization,
  IdentityTenant,
  PageData,
  PermissionDefinition,
  StaffAccount,
  StaffMembership,
  WebLoginDomain,
} from '@/types';

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

function operationUid(): string {
  return crypto.randomUUID();
}

function scopedBase(context: DirectoryContext): string {
  if (context.domain === 'platform') {
    if (!context.tenantCode) {
      throw new Error('平台目录操作需要先选择目标租户');
    }
    return `/api/v1/web/platform/tenants/${context.tenantCode}`;
  }
  return '/api/v1/web';
}

function write<T>(url: string, method: 'POST' | 'PUT', data: unknown) {
  return request<T>({
    url,
    method,
    data,
    idempotencyKey: operationUid(),
  });
}

export function listIdentityTenants(params: DirectoryPageParams = {}) {
  return request<PageData<IdentityTenant>>({
    url: '/api/v1/web/platform/tenants',
    method: 'GET',
    params,
  });
}

export function getIdentityTenant(tenantCode: string) {
  return request<IdentityTenant>({
    url: `/api/v1/web/platform/tenants/${tenantCode}`,
    method: 'GET',
  });
}

export function createIdentityTenant(data: {
  tenantCode: string;
  enterpriseName: string;
  contactName?: string;
  contactPhone?: string;
  contactAddress?: string;
}) {
  return write<IdentityTenant>(
    '/api/v1/web/platform/tenants',
    'POST',
    data,
  );
}

export function updateIdentityTenant(
  tenantCode: string,
  data: TenantProfileInput,
) {
  return write<IdentityTenant>(
    `/api/v1/web/platform/tenants/${tenantCode}/profile`,
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
) {
  return write<StaffAccount>(
    `/api/v1/web/platform/tenants/${tenantCode}/principal-account`,
    'POST',
    data,
  );
}

export function changeTenantStatus(
  tenantCode: string,
  enabled: boolean,
  expectedVersion: number,
  reason?: string,
) {
  return write<IdentityTenant>(
    `/api/v1/web/platform/tenants/${tenantCode}/${
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

export function updateCurrentTenant(data: TenantProfileInput) {
  return write<IdentityTenant>(
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

export function createOrganization(
  context: DirectoryContext,
  data: {
    organizationCode: string;
    organizationName: string;
    contactPhone?: string;
    contactAddress?: string;
  },
) {
  return write<IdentityOrganization>(
    `${scopedBase(context)}/organizations`,
    'POST',
    data,
  );
}

export function updateOrganization(
  context: DirectoryContext,
  organizationCode: string,
  data: OrganizationProfileInput,
) {
  return write<IdentityOrganization>(
    `${scopedBase(context)}/organizations/${organizationCode}/profile`,
    'PUT',
    data,
  );
}

export function changeOrganizationStatus(
  context: DirectoryContext,
  organization: IdentityOrganization,
  enabled: boolean,
  reason?: string,
) {
  return write<IdentityOrganization>(
    `${scopedBase(context)}/organizations/${
      organization.organizationCode
    }/${enabled ? 'activations' : 'deactivations'}`,
    'POST',
    { expectedVersion: organization.version, reason },
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

export function createStaffAccount(
  context: DirectoryContext,
  data: {
    loginName: string;
    initialPassword: string;
    displayName: string;
    contactPhone?: string;
    permissionCodes: string[];
  },
) {
  return write<StaffAccount>(
    `${scopedBase(context)}/staff-accounts`,
    'POST',
    data,
  );
}

export function updateStaffAccount(
  context: DirectoryContext,
  staffUid: string,
  data: StaffProfileInput,
) {
  return write<StaffAccount>(
    `${scopedBase(context)}/staff-accounts/${staffUid}/profile`,
    'PUT',
    data,
  );
}

export function changeStaffStatus(
  context: DirectoryContext,
  staff: StaffAccount,
  enabled: boolean,
  reason?: string,
) {
  return write<StaffAccount>(
    `${scopedBase(context)}/staff-accounts/${
      staff.staffAccountUid
    }/${enabled ? 'activations' : 'deactivations'}`,
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
) {
  return write<StaffAccount>(
    `${scopedBase(context)}/staff-accounts/${
      staff.staffAccountUid
    }/password-resets`,
    'POST',
    {
      newPassword,
      expectedVersion: staff.version,
      expectedAuthVersion: staff.authVersion,
    },
  );
}

export function updateOwnProfile(data: StaffProfileInput) {
  return write<StaffAccount>(
    '/api/v1/web/staff-accounts/current/profile',
    'PUT',
    data,
  );
}

export function changeOwnPassword(data: {
  currentPassword: string;
  newPassword: string;
  expectedVersion: number;
  expectedAuthVersion: number;
}) {
  return write<StaffAccount>(
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
    url: `${scopedBase(context)}/staff-accounts/${staffUid}/effective-access`,
    method: 'GET',
  });
}

export function replaceTenantPermissions(
  context: DirectoryContext,
  staffUid: string,
  permissionCodes: string[],
  expectedAuthVersion: number,
) {
  return write<EffectiveAccess>(
    `${scopedBase(context)}/staff-accounts/${staffUid}/tenant-permissions`,
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
    url: `${scopedBase(context)}/organizations/${organizationCode}/staff-memberships`,
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
) {
  return write<StaffMembership>(
    `${scopedBase(context)}/organizations/${organizationCode}/staff-memberships`,
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
) {
  return write<{
    staffAccount: StaffAccount;
    membership: StaffMembership;
  }>(
    `${scopedBase(context)}/organizations/${organizationCode}/staff-account-provisionings`,
    'POST',
    data,
  );
}

export function replaceMembershipAuthorization(
  context: DirectoryContext,
  membership: StaffMembership,
  manager: boolean,
  permissionCodes: string[],
) {
  return write<StaffMembership>(
    `${scopedBase(context)}/organizations/${
      membership.organizationCode
    }/staff-memberships/${
      membership.staffAccountUid
    }/authorization`,
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
  manager = false,
  permissionCodes: string[] = [],
  reason?: string,
) {
  return write<StaffMembership>(
    `${scopedBase(context)}/organizations/${
      membership.organizationCode
    }/staff-memberships/${
      membership.staffAccountUid
    }/${enabled ? 'activations' : 'deactivations'}`,
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
