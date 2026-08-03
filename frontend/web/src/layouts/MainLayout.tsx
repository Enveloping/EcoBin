import { ProLayout } from '@ant-design/pro-components';
import { useEffect, useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Dropdown } from 'antd';
import { LogoutOutlined, UserOutlined, SettingOutlined } from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';
import { type AppMenuRoute, menuRoutesFor } from '@/router/routes';
import { menuTargetPath } from '@/router/directoryQuery';
import { logout } from '@/api/auth';
import { palette, alpha } from '@/theme';
import EcoBinLogo from '@/components/Logo';

function toMenuData(route: AppMenuRoute): AppMenuRoute {
  return {
    ...route,
    routes: route.routes?.map(toMenuData),
  };
}

function selectedMenuKey(pathname: string, search: string): string {
  const view = new URLSearchParams(search).get('view');
  if (pathname === '/tenant') {
    return view === 'disabled'
      ? '/menu/tenants/disabled'
      : '/menu/tenants/all';
  }
  if (pathname === '/organization-users') {
    return view === 'disabled'
      ? '/menu/organization-users/disabled'
      : '/menu/organization-users/all';
  }
  return pathname;
}

function activeMenuParent(pathname: string): string | undefined {
  const parentByPath: Record<string, string> = {
    '/tenant': '/menu/tenants',
    '/organization-users': '/menu/organization-users',
    '/deliveries': '/menu/deliveries',
    '/clean-records': '/menu/clean-records',
    '/funds': '/menu/funds',
    '/withdrawals': '/menu/funds',
  };
  return parentByPath[pathname];
}

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { session, domain, clear } = useAuthStore();
  const activeParent = activeMenuParent(location.pathname);
  const [openKeys, setOpenKeys] = useState<string[]>(
    activeParent ? [activeParent] : [],
  );

  const menuData = menuRoutesFor(session).map(toMenuData);

  useEffect(() => {
    if (!activeParent) return;
    setOpenKeys((current) => (
      current.includes(activeParent) ? current : [...current, activeParent]
    ));
  }, [activeParent]);

  const handleLogout = async () => {
    try {
      if (domain) await logout(domain);
    } finally {
      clear();
      navigate('/login', { replace: true });
    }
  };

  return (
    <>
      <a className="skip-link" href="#main-content">
        跳到主内容
      </a>
      <ProLayout
      title=""
      logo={<EcoBinLogo collapsed={false} />}
      layout="mix"
      fixedHeader
      fixSiderbar
      location={{ pathname: selectedMenuKey(location.pathname, location.search) }}
      route={{ path: '/', routes: menuData }}
      siderWidth={220}
      collapsedButtonRender={false}
      siderMenuType="sub"
      // 浅色侧边栏：白底 + 绿色选中高亮
      menuProps={{
        selectedKeys: [
          selectedMenuKey(location.pathname, location.search),
        ],
        openKeys,
        onOpenChange: setOpenKeys,
        style: {
          background: palette.bgContainer,
          borderRight: `1px solid ${palette.border}`,
        },
      }}
      menuItemRender={(item, dom) => (
        item.disabled || !item.path
          ? <span title={item.tooltip}>{dom}</span>
          : (
              <a
                title={item.tooltip}
                onClick={() => navigate(menuTargetPath(
                  (item as AppMenuRoute).targetPath ?? item.path!,
                  location.search,
                  domain === 'platform',
                ))}
              >
                {dom}
              </a>
            )
      )}
      // 侧边栏 token：浅色 + 主色高亮（通过 ProLayout 全局 token 覆盖）
      token={{
        sider: {
          colorMenuBackground: palette.bgContainer,
          colorTextMenu: palette.textRegular,
          colorTextMenuSecondary: palette.textSecondary,
          colorTextMenuSelected: palette.primary,
          colorBgMenuItemSelected: alpha(palette.primaryRGB, 0.1),
          colorTextMenuActive: palette.primary,
          colorTextMenuItemHover: palette.primary,
        },
        // 顶部导航栏样式（ProLayout 通过 token.header 控制，headerStyle 已不生效）
        header: {
          colorBgHeader: palette.bgContainer,
        },
      }}
      // 右上角用户头像
      avatarProps={{
        icon: <UserOutlined style={{ color: palette.primary }} />,
        title: session?.displayName || '用户',
        size: 'small',
        style: { cursor: 'pointer' },
        render: (_props, dom) => (
          <Dropdown
            menu={{
              items: [
                {
                  key: 'role',
                  disabled: true,
                  icon: <SettingOutlined />,
                  label: `账号类型：${session?.accountType ?? '-'}`,
                },
                { type: 'divider' },
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  danger: true,
                  onClick: handleLogout,
                },
              ],
            }}
            placement="bottomRight"
          >
            {dom}
          </Dropdown>
        ),
      }}
      // 内容区域样式
      contentStyle={{
        minHeight: 'calc(100dvh - 64px)',
        padding: 24,
        background: palette.bgLayout,
      }}
    >
        <main id="main-content" className="workspace-main" tabIndex={-1}>
          <Outlet />
        </main>
      </ProLayout>
    </>
  );
}
