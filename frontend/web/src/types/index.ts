import type { components } from '@/api/generated/openapi';

type Schemas = components['schemas'];

/**
 * Stable application aliases around the generated OpenAPI types.
 * Pages import from here so regenerating the machine contract never couples UI
 * code to the generated file layout.
 */
export type LoginResponse = Schemas['WebSession'];
export type WebAccountType = LoginResponse['accountType'];
export type OrganizationSummary = LoginResponse['organizations'][number];
export type DirectoryStatus = Schemas['DirectoryStatus'];
export type PrincipalAccountSummary = Schemas['PrincipalAccountSummary'];
export type IdentityTenant = Schemas['IdentityTenant'];
export type IdentityOrganization = Schemas['IdentityOrganization'];
export type StaffAccount = Schemas['StaffAccount'];
export type StaffAccountKind = StaffAccount['accountKind'];
export type PermissionDefinition = Schemas['PermissionDefinition'];
export type PermissionScopeKind = PermissionDefinition['scopeKind'];
export type EffectiveAccess = Schemas['EffectiveAccess'];
export type OrganizationEffectiveAccess = EffectiveAccess['organizations'][number];
export type StaffMembership = Schemas['OrganizationMembership'];
export type OrganizationUser = Schemas['OrganizationUser'];
export type OrganizationUserRegistrationSource =
  Schemas['OrganizationUserRegistrationSource'];

export type WebLoginDomain = 'platform' | 'tenant';

export interface LoginRequest {
  loginName: string;
  password: string;
}

/** Typed pagination facade used after the response envelope is unwrapped. */
export interface PageData<T> {
  items: T[];
  page: number;
  pageSize: number;
  total: number;
}

export interface CursorPageData<T> {
  items: T[];
  nextCursor: string | null;
  asOf: string;
}
