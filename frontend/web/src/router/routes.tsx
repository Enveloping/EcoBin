import type { ReactNode } from 'react';
import {
  ApartmentOutlined,
  BankOutlined,
  IdcardOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import type { LoginResponse, WebAccountType } from '@/types';

import TenantPage from '@/pages/tenant';
import MyTenantPage from '@/pages/tenant/MyTenant';
import OrganizationPage from '@/pages/organization';
import StaffPage from '@/pages/staff';
import AccessPage from '@/pages/access';
import AccountSettingsPage from '@/pages/account';

export interface AppRoute {
  path: string;
  name?: string;
  icon?: ReactNode;
  element: ReactNode;
  capability?: string;
  accountTypes?: WebAccountType[];
}

const PLATFORM: WebAccountType[] = ['PLATFORM_ADMIN'];
const TENANT_WEB: WebAccountType[] = ['TENANT_PRINCIPAL', 'STAFF'];

/**
 * V-01 only exposes pages backed by the target /api/v1 identity contract.
 * Downstream device, recycling and funds pages return when their vertical
 * slices migrate; hiding them prevents the target Cookie session from falling
 * through to legacy Bearer endpoints.
 */
export const appRoutes: AppRoute[] = [
  {
    path: '/tenant',
    name: '租户管理',
    icon: <ApartmentOutlined />,
    element: <TenantPage />,
    capability: 'tenant.read',
    accountTypes: PLATFORM,
  },
  {
    path: '/my-tenant',
    name: '我的租户',
    icon: <IdcardOutlined />,
    element: <MyTenantPage />,
    capability: 'tenant.read',
    accountTypes: TENANT_WEB,
  },
  {
    path: '/organizations',
    name: '机构管理',
    icon: <BankOutlined />,
    element: <OrganizationPage />,
    capability: 'organization.read',
  },
  {
    path: '/staff',
    name: '工作人员',
    icon: <TeamOutlined />,
    element: <StaffPage />,
    capability: 'staff.read',
  },
  {
    path: '/access',
    name: '任职与授权',
    icon: <SafetyCertificateOutlined />,
    element: <AccessPage />,
    capability: 'permission.read',
  },
  {
    path: '/account',
    name: '账号设置',
    icon: <SettingOutlined />,
    element: <AccountSettingsPage />,
    accountTypes: TENANT_WEB,
  },
];

export function canAccessRoute(
  session: LoginResponse | null,
  route: Pick<AppRoute, 'capability' | 'accountTypes'>,
): boolean {
  if (!session) return false;
  if (route.accountTypes && !route.accountTypes.includes(session.accountType)) {
    return false;
  }
  return !route.capability || session.capabilities.includes(route.capability);
}

export function menuRoutesFor(session: LoginResponse | null): AppRoute[] {
  return appRoutes.filter(
    (route) => route.name && canAccessRoute(session, route),
  );
}

export function defaultPathFor(session: LoginResponse | null): string {
  return menuRoutesFor(session)[0]?.path ?? '/account';
}
