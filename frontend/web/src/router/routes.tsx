import type { ReactNode } from 'react';
import {
  DashboardOutlined,
  SafetyCertificateOutlined,
  ApartmentOutlined,
  IdcardOutlined,
  TeamOutlined,
  HddOutlined,
  InboxOutlined,
  CarOutlined,
  WalletOutlined,
  BarChartOutlined,
} from '@ant-design/icons';
import type { LoginResponse, WebAccountType } from '@/types';

import Dashboard from '@/pages/dashboard';
import AdminPage from '@/pages/admin';
import TenantPage from '@/pages/tenant';
import MyTenantPage from '@/pages/tenant/MyTenant';
import UserPage from '@/pages/user';
import DevicePage from '@/pages/device';
import DoorPage from '@/pages/door';
import DeliveryPage from '@/pages/delivery';
import CleanPage from '@/pages/clean';
import WithdrawPage from '@/pages/withdraw';
import StatisticsPage from '@/pages/statistics';

export interface AppRoute {
  path: string;
  name?: string;
  icon?: ReactNode;
  element: ReactNode;
  capability: string;
  accountTypes?: WebAccountType[];
}

const PLATFORM: WebAccountType[] = ['PLATFORM_ADMIN'];
const TENANT_WEB: WebAccountType[] = ['TENANT_PRINCIPAL', 'STAFF'];

export const appRoutes: AppRoute[] = [
  {
    path: '/dashboard',
    name: '仪表盘',
    icon: <DashboardOutlined />,
    element: <Dashboard />,
    capability: 'overview.read',
  },
  {
    path: '/admin',
    name: '管理员管理',
    icon: <SafetyCertificateOutlined />,
    element: <AdminPage />,
    capability: 'platform-admin.read',
    accountTypes: PLATFORM,
  },
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
    path: '/user',
    name: '用户管理',
    icon: <TeamOutlined />,
    element: <UserPage />,
    capability: 'user.read',
  },
  {
    path: '/device',
    name: '设备管理',
    icon: <HddOutlined />,
    element: <DevicePage />,
    capability: 'device.read',
  },
  {
    path: '/device/:deviceId/doors',
    element: <DoorPage />,
    capability: 'device.read',
  },
  {
    path: '/delivery',
    name: '投递订单',
    icon: <InboxOutlined />,
    element: <DeliveryPage />,
    capability: 'delivery.read',
  },
  {
    path: '/clean',
    name: '清运记录',
    icon: <CarOutlined />,
    element: <CleanPage />,
    capability: 'clean.read',
  },
  {
    path: '/withdraw',
    name: '提现审核',
    icon: <WalletOutlined />,
    element: <WithdrawPage />,
    capability: 'withdrawal.read',
  },
  {
    path: '/statistics',
    name: '业务统计',
    icon: <BarChartOutlined />,
    element: <StatisticsPage />,
    capability: 'overview.read',
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
  return session.capabilities.includes(route.capability);
}

export function menuRoutesFor(session: LoginResponse | null): AppRoute[] {
  return appRoutes.filter(
    (route) => route.name && canAccessRoute(session, route),
  );
}

export function defaultPathFor(session: LoginResponse | null): string {
  return menuRoutesFor(session)[0]?.path ?? '/dashboard';
}
