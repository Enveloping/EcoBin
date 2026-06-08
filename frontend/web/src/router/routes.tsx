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
import { ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_TENANT } from '@/constants';

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
  /** 有 name 才进菜单；无则为隐藏路由 */
  name?: string;
  icon?: ReactNode;
  element: ReactNode;
  roles: number[];
}

const SUPER = ROLE_SUPER_ADMIN;
const ADMIN = ROLE_ADMIN;
const TENANT = ROLE_TENANT;

export const appRoutes: AppRoute[] = [
  {
    path: '/dashboard',
    name: '仪表盘',
    icon: <DashboardOutlined />,
    element: <Dashboard />,
    roles: [SUPER, TENANT],
  },
  {
    path: '/admin',
    name: '管理员管理',
    icon: <SafetyCertificateOutlined />,
    element: <AdminPage />,
    roles: [SUPER],
  },
  {
    path: '/tenant',
    name: '租户管理',
    icon: <ApartmentOutlined />,
    element: <TenantPage />,
    roles: [SUPER, ADMIN],
  },
  {
    path: '/my-tenant',
    name: '我的租户',
    icon: <IdcardOutlined />,
    element: <MyTenantPage />,
    roles: [TENANT],
  },
  {
    path: '/user',
    name: '用户管理',
    icon: <TeamOutlined />,
    element: <UserPage />,
    roles: [SUPER, TENANT],
  },
  {
    path: '/device',
    name: '设备管理',
    icon: <HddOutlined />,
    element: <DevicePage />,
    roles: [SUPER, ADMIN, TENANT],
  },
  {
    // 投口管理：从设备页进入，不在菜单
    path: '/device/:deviceId/doors',
    element: <DoorPage />,
    roles: [SUPER, ADMIN, TENANT],
  },
  {
    path: '/delivery',
    name: '投递订单',
    icon: <InboxOutlined />,
    element: <DeliveryPage />,
    roles: [SUPER, TENANT],
  },
  {
    path: '/clean',
    name: '清运审核',
    icon: <CarOutlined />,
    element: <CleanPage />,
    roles: [SUPER, TENANT],
  },
  {
    path: '/withdraw',
    name: '提现审核',
    icon: <WalletOutlined />,
    element: <WithdrawPage />,
    roles: [SUPER, TENANT],
  },
  {
    path: '/statistics',
    name: '业务统计',
    icon: <BarChartOutlined />,
    element: <StatisticsPage />,
    roles: [SUPER, TENANT],
  },
];

/** 当前角色可见的菜单项（有 name 的） */
export function menuRoutesFor(role: number | null): AppRoute[] {
  if (role == null) return [];
  return appRoutes.filter((r) => r.name && r.roles.includes(role));
}

/** 当前角色的默认落地路由 */
export function defaultPathFor(role: number | null): string {
  const menus = menuRoutesFor(role);
  return menus[0]?.path ?? '/dashboard';
}
