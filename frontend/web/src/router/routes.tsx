import { lazy, type ReactNode } from 'react';
import {
  ApartmentOutlined,
  BankOutlined,
  CloudServerOutlined,
  DollarOutlined,
  IdcardOutlined,
  QrcodeOutlined,
  SafetyCertificateOutlined,
  ShoppingCartOutlined,
  SettingOutlined,
  SlidersOutlined,
  TeamOutlined,
  ToolOutlined,
  TruckOutlined,
  UserOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import { Navigate, useLocation } from 'react-router-dom';
import type { LoginResponse, WebAccountType } from '@/types';
import { hasRouteAccess } from './access';
import { directoryPath } from './directoryQuery';

const TenantPage = lazy(() => import('@/pages/tenant'));
const PlatformAdministratorsPage = lazy(
  () => import('@/pages/platform-admins'),
);
const FactoryOperatorsPage = lazy(
  () => import('@/pages/factory-operators'),
);
const MyTenantPage = lazy(() => import('@/pages/tenant/MyTenant'));
const OrganizationPage = lazy(() => import('@/pages/organization'));
const OrganizationUserPage = lazy(() => import('@/pages/organization-user'));
const StaffPage = lazy(() => import('@/pages/staff'));
const AccountSettingsPage = lazy(() => import('@/pages/account'));
const DeviceManagementPage = lazy(
  () => import('@/pages/device-management'),
);
const BagLabelsPage = lazy(() => import('@/pages/bag-labels'));
const DeliveryOrdersPage = lazy(
  () => import('@/pages/delivery-orders'),
);
const DeliveryConfigurationPage = lazy(
  () => import('@/pages/delivery-configuration'),
);
const CleanOperationsPage = lazy(
  () => import('@/pages/clean-operations'),
);
const CleanRecordsPage = lazy(
  () => import('@/pages/clean-records'),
);
const FundsPage = lazy(() => import('@/pages/funds'));
const WithdrawalsPage = lazy(() => import('@/pages/withdrawals'));
const WalletEntriesPage = lazy(
  () => import('@/pages/wallet-entries'),
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
const ALL_WEB_ACCOUNTS: WebAccountType[] = [...PLATFORM, ...TENANT_WEB];

function LegacyUserBindingsRedirect() {
  const location = useLocation();
  const source = new URLSearchParams(location.search);
  return (
    <Navigate
      replace
      to={directoryPath('/organization-users', {
        tenant: source.get('tenant') ?? undefined,
        organization: source.get('organization') ?? undefined,
      })}
    />
  );
}

/**
 * Only pages backed by the target /api/v1 identity contract are exposed.
 * Downstream device, recycling and funds pages return with their vertical
 * contracts; hidden legacy Bearer pages have been removed from the tree.
 */
export const appRoutes: AppRoute[] = [
  {
    path: '/platform-admins',
    name: '平台管理员',
    icon: <SafetyCertificateOutlined />,
    element: <PlatformAdministratorsPage />,
    allOf: ['platform-account.manage'],
    accountTypes: PLATFORM,
  },
  {
    path: '/factory-operators',
    name: '厂家操作员',
    icon: <ToolOutlined />,
    element: <FactoryOperatorsPage />,
    allOf: ['platform-admin.manage'],
    accountTypes: PLATFORM,
  },
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
    element: <LegacyUserBindingsRedirect />,
  },
  {
    path: '/devices',
    name: '设备管理',
    icon: <CloudServerOutlined />,
    element: <DeviceManagementPage />,
    allOf: ['device.read'],
  },
  {
    path: '/bag-labels',
    name: '袋码管理',
    icon: <QrcodeOutlined />,
    element: <BagLabelsPage />,
    allOf: ['platform-admin.manage'],
    accountTypes: PLATFORM,
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
    path: '/wallet-entries',
    name: '钱包流水',
    icon: <WalletOutlined />,
    element: <WalletEntriesPage />,
    allOf: ['wallet.read'],
  },
  {
    path: '/clean-operations',
    name: '清运操作',
    icon: <TruckOutlined />,
    element: <CleanOperationsPage />,
    allOf: ['clean.read'],
  },
  {
    path: '/clean-records',
    name: '清运记录',
    icon: <TruckOutlined />,
    element: <CleanRecordsPage />,
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
    accountTypes: ALL_WEB_ACCOUNTS,
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
  const platformAdministrators = visibleRoute(session, '/platform-admins');
  if (platformAdministrators) menu.push(leaf(platformAdministrators));
  const factoryOperators = visibleRoute(session, '/factory-operators');
  if (factoryOperators) menu.push(leaf(factoryOperators));
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

  const walletEntries = visibleRoute(session, '/wallet-entries');
  if (walletEntries) menu.push(leaf(walletEntries));

  for (const path of ['/staff', '/devices', '/bag-labels'] as const) {
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

  const cleanOperations = visibleRoute(session, '/clean-operations');
  const cleanRecords = visibleRoute(session, '/clean-records');
  if (cleanOperations || cleanRecords) {
    menu.push({
      path: '/menu/cleaning',
      name: '清运管理',
      icon: cleanOperations?.icon ?? cleanRecords?.icon,
      routes: [cleanOperations, cleanRecords]
        .filter((route): route is AppRoute => !!route)
        .map((route) => leaf(
          route,
          route.path,
          route.name ?? '',
          false,
        )),
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
