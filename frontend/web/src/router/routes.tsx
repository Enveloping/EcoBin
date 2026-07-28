import { lazy, type ReactNode } from 'react';
import {
  ApartmentOutlined,
  BankOutlined,
  IdcardOutlined,
  LinkOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { LoginResponse, WebAccountType } from '@/types';
import { hasRouteAccess } from './access';

const TenantPage = lazy(() => import('@/pages/tenant'));
const MyTenantPage = lazy(() => import('@/pages/tenant/MyTenant'));
const OrganizationPage = lazy(() => import('@/pages/organization'));
const OrganizationUserPage = lazy(() => import('@/pages/organization-user'));
const StaffPage = lazy(() => import('@/pages/staff'));
const AccessPage = lazy(() => import('@/pages/access'));
const AccountSettingsPage = lazy(() => import('@/pages/account'));
const OrganizationUserBindingPage = lazy(
  () => import('@/pages/user/OrganizationUserBinding'),
);

export interface AppRoute {
  path: string;
  name?: string;
  icon?: ReactNode;
  element: ReactNode;
  allOf?: string[];
  anyOf?: string[];
  accountTypes?: WebAccountType[];
}

const PLATFORM: WebAccountType[] = ['PLATFORM_ADMIN'];
const TENANT_WEB: WebAccountType[] = ['TENANT_PRINCIPAL', 'STAFF'];

/**
 * Only pages backed by the target /api/v1 identity contract are exposed.
 * Downstream device, recycling and funds pages return with their vertical
 * contracts; hidden legacy Bearer pages have been removed from the tree.
 */
export const appRoutes: AppRoute[] = [
  {
    path: '/tenant',
    name: '租户管理',
    icon: <ApartmentOutlined />,
    element: <TenantPage />,
    allOf: ['tenant.read'],
    accountTypes: PLATFORM,
  },
  {
    path: '/my-tenant',
    name: '我的租户',
    icon: <IdcardOutlined />,
    element: <MyTenantPage />,
    allOf: ['tenant.read'],
    accountTypes: TENANT_WEB,
  },
  {
    path: '/organizations',
    name: '机构管理',
    icon: <BankOutlined />,
    element: <OrganizationPage />,
    allOf: ['organization.read'],
  },
  {
    path: '/organization-users',
    name: '机构用户',
    icon: <UserOutlined />,
    element: <OrganizationUserPage />,
    allOf: ['user.read'],
  },
  {
    path: '/staff',
    name: '工作人员',
    icon: <TeamOutlined />,
    element: <StaffPage />,
    allOf: ['staff.read'],
  },
  {
    path: '/user-bindings',
    name: '用户绑定',
    icon: <LinkOutlined />,
    element: <OrganizationUserBindingPage />,
    allOf: ['user.read', 'staff.bind'],
  },
  {
    path: '/access',
    name: '任职与授权',
    icon: <SafetyCertificateOutlined />,
    element: <AccessPage />,
    allOf: ['permission.read'],
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
  route: Pick<AppRoute, 'allOf' | 'anyOf' | 'accountTypes'>,
): boolean {
  return hasRouteAccess(session, route);
}

export function menuRoutesFor(session: LoginResponse | null): AppRoute[] {
  return appRoutes.filter(
    (route) => route.name && canAccessRoute(session, route),
  );
}

export function defaultPathFor(session: LoginResponse | null): string {
  return menuRoutesFor(session)[0]?.path ?? '/not-found';
}
