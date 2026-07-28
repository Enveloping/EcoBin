import { ProLayout } from '@ant-design/pro-components';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Dropdown } from 'antd';
import { LogoutOutlined, UserOutlined, SettingOutlined } from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';
import { menuRoutesFor } from '@/router/routes';
import { logout } from '@/api/auth';
import { palette, alpha } from '@/theme';
import EcoBinLogo from '@/components/Logo';

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { session, domain, clear } = useAuthStore();
  const targetTenant = domain === 'platform'
    ? new URLSearchParams(location.search).get('tenant')
    : null;

  const menuData = menuRoutesFor(session).map((r) => ({
    path: r.path,
    name: r.name,
    icon: r.icon,
  }));

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
      location={{ pathname: location.pathname }}
      route={{ path: '/', routes: menuData }}
      siderWidth={220}
      collapsedButtonRender={false}
      siderMenuType="group"
      // 浅色侧边栏：白底 + 绿色选中高亮
      menuProps={{
        style: {
          background: palette.bgContainer,
          borderRight: `1px solid ${palette.border}`,
        },
      }}
      menuItemRender={(item, dom) => (
        <a
          onClick={() => {
            if (!item.path) return;
            const search = targetTenant
              ? `?tenant=${encodeURIComponent(targetTenant)}`
              : '';
            navigate(`${item.path}${search}`);
          }}
        >
          {dom}
        </a>
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
