import { lazy, type ReactNode } from 'react';
import {
  ApartmentOutlined,
  BankOutlined,
  CloudServerOutlined,
  DollarOutlined,
  IdcardOutlined,
  LinkOutlined,
  ShoppingCartOutlined,
  SettingOutlined,
  SlidersOutlined,
  TeamOutlined,
  TruckOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Navigate } from 'react-router-dom';
import type { LoginResponse, WebAccountType } from '@/types';
import { hasRouteAccess } from './access';

const TenantPage = lazy(() => import('@/pages/tenant'));
const MyTenantPage = lazy(() => import('@/pages/tenant/MyTenant'));
const OrganizationPage = lazy(() => import('@/pages/organization'));
const OrganizationUserPage = lazy(() => import('@/pages/organization-user'));
const StaffPage = lazy(() => import('@/pages/staff'));
const AccountSettingsPage = lazy(() => import('@/pages/account'));
const DeviceManagementPage = lazy(
  () => import('@/pages/device-management'),
);
const DeliveryOrdersPage = lazy(
  () => import('@/pages/delivery-orders'),
);
const DeliveryConfigurationPage = lazy(
  () => import('@/pages/delivery-configuration'),
);
const FundsPage = lazy(() => import('@/pages/funds'));
const WithdrawalsPage = lazy(() => import('@/pages/withdrawals'));
const BusinessContractPendingPage = lazy(
  () => import('@/pages/business/BusinessContractPending'),
);
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

export interface AppMenuRoute {
  path?: string;
  targetPath?: string;
  name: string;
  icon?: ReactNode;
  disabled?: boolean;
  tooltip?: string;
  routes?: AppMenuRoute[];
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
    path: '/devices',
    name: '设备管理',
    icon: <CloudServerOutlined />,
    element: <DeviceManagementPage />,
    allOf: ['device.read'],
  },
  {
    path: '/deliveries',
    name: '投递订单',
    icon: <ShoppingCartOutlined />,
    element: <DeliveryOrdersPage />,
    anyOf: ['delivery.read', 'review.execute'],
  },
  {
    path: '/delivery-configuration',
    name: '投递与审核规则',
    icon: <SlidersOutlined />,
    element: <DeliveryConfigurationPage />,
    allOf: ['delivery.configuration.manage'],
  },
  {
    path: '/clean-records',
    name: '清运订单',
    icon: <TruckOutlined />,
    element: <BusinessContractPendingPage kind="cleaning" />,
    allOf: ['clean.read'],
  },
  {
    path: '/funds',
    name: '机构资金',
    icon: <DollarOutlined />,
    element: <FundsPage />,
    anyOf: ['fund.read', 'recharge.create'],
  },
  {
    path: '/withdrawals',
    name: '提现订单',
    icon: <DollarOutlined />,
    element: <WithdrawalsPage />,
    anyOf: ['withdrawal.read', 'review.execute'],
  },
  {
    path: '/access',
    element: <Navigate to="/staff" replace />,
    allOf: ['staff.read'],
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

function visibleRoute(
  session: LoginResponse | null,
  path: string,
): AppRoute | undefined {
  const route = appRoutes.find((candidate) => candidate.path === path);
  return route && canAccessRoute(session, route) ? route : undefined;
}

function leaf(
  route: AppRoute,
  path = route.path,
  name = route.name ?? '',
  withIcon = true,
  targetPath = path,
) {
  return {
    path,
    targetPath,
    name,
    icon: withIcon ? route.icon : undefined,
  } satisfies AppMenuRoute;
}

export function menuRoutesFor(
  session: LoginResponse | null,
): AppMenuRoute[] {
  const menu: AppMenuRoute[] = [];
  const tenant = visibleRoute(session, '/tenant');
  if (tenant) {
    menu.push({
      path: '/menu/tenants',
      name: '租户管理',
      icon: tenant.icon,
      routes: [
        leaf(
          tenant,
          '/menu/tenants/all',
          '所有租户',
          false,
          '/tenant',
        ),
        leaf(
          tenant,
          '/menu/tenants/disabled',
          '已禁用的租户',
          false,
          '/tenant?view=disabled',
        ),
      ],
    });
  }

  for (const path of ['/my-tenant', '/organizations'] as const) {
    const route = visibleRoute(session, path);
    if (route) menu.push(leaf(route));
  }

  const organizationUsers = visibleRoute(session, '/organization-users');
  if (organizationUsers) {
    menu.push({
      path: '/menu/organization-users',
      name: '机构用户',
      icon: organizationUsers.icon,
      routes: [
        leaf(
          organizationUsers,
          '/menu/organization-users/all',
          '所有用户',
          false,
          '/organization-users',
        ),
        leaf(
          organizationUsers,
          '/menu/organization-users/disabled',
          '已禁用的用户',
          false,
          '/organization-users?view=disabled',
        ),
      ],
    });
  }

  for (const path of ['/staff', '/user-bindings', '/devices'] as const) {
    const route = visibleRoute(session, path);
    if (route) menu.push(leaf(route));
  }

  const delivery = visibleRoute(session, '/deliveries');
  const deliveryConfiguration = visibleRoute(
    session,
    '/delivery-configuration',
  );
  if (delivery || deliveryConfiguration) {
    const deliveryRoutes: AppMenuRoute[] = [];
    if (delivery) {
      deliveryRoutes.push(
        leaf(delivery, delivery.path, delivery.name ?? '', false),
        {
          path: '/menu/deliveries/rejected',
          name: '已拒绝订单',
          disabled: true,
          tooltip: '目标投递契约没有“拒绝”终态',
        },
        {
          path: '/menu/deliveries/corrected',
          name: '已纠正订单',
          disabled: true,
          tooltip: '目标契约尚未提供仅看纠正订单的列表筛选',
        },
      );
    }
    if (deliveryConfiguration) {
      deliveryRoutes.push(
        leaf(
          deliveryConfiguration,
          deliveryConfiguration.path,
          deliveryConfiguration.name ?? '',
          false,
        ),
      );
    }
    menu.push({
      path: '/menu/deliveries',
      name: '投递管理',
      icon: delivery?.icon ?? deliveryConfiguration?.icon,
      routes: deliveryRoutes,
    });
  }

  const cleaning = visibleRoute(session, '/clean-records');
  if (cleaning) {
    menu.push({
      path: '/menu/clean-records',
      name: '清运订单',
      icon: cleaning.icon,
      routes: [
        leaf(cleaning, cleaning.path, cleaning.name ?? '', false),
        {
          path: '/menu/clean-records/invalid',
          name: '无效清运订单',
          disabled: true,
          tooltip: '目标契约尚未定义“无效清运订单”终态',
        },
      ],
    });
  }

  const funds = visibleRoute(session, '/funds');
  const withdrawal = visibleRoute(session, '/withdrawals');
  if (funds || withdrawal) {
    const fundsRoutes: AppMenuRoute[] = [];
    if (funds) fundsRoutes.push(leaf(funds, funds.path, funds.name ?? '', false));
    if (withdrawal) fundsRoutes.push(leaf(withdrawal, withdrawal.path, withdrawal.name ?? '', false));
    menu.push({
      path: '/menu/funds',
      name: '资金管理',
      icon: funds?.icon ?? withdrawal?.icon,
      routes: fundsRoutes,
    });
  }

  const account = visibleRoute(session, '/account');
  if (account) menu.push(leaf(account));
  return menu;
}

export function defaultPathFor(session: LoginResponse | null): string {
  const first = menuRoutesFor(session)[0];
  const firstChild = first?.routes?.[0];
  return firstChild?.targetPath
    ?? firstChild?.path
    ?? first?.targetPath
    ?? first?.path
    ?? '/not-found';
}
