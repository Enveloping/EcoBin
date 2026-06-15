import { ProLayout } from '@ant-design/pro-components';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Dropdown } from 'antd';
import { LogoutOutlined, UserOutlined, SettingOutlined } from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';
import { menuRoutesFor } from '@/router/routes';
import { ROLE } from '@/constants';
import { palette, alpha } from '@/theme';
import EcoBinLogo from '@/components/Logo';

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { role, realName, username, clear } = useAuthStore();

  const menuData = menuRoutesFor(role).map((r) => ({
    path: r.path,
    name: r.name,
    icon: r.icon,
  }));

  const handleLogout = () => {
    clear();
    navigate('/login', { replace: true });
  };

  return (
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
          background: '#FFFFFF',
          borderRight: '1px solid #E2E8F0',
        },
      }}
      menuItemRender={(item, dom) => (
        <a onClick={() => item.path && navigate(item.path)}>{dom}</a>
      )}
      // 侧边栏 token：浅色 + 主色高亮（通过 ProLayout 全局 token 覆盖）
      token={{
        sider: {
          colorMenuBackground: '#FFFFFF',
          colorTextMenu: palette.textRegular,
          colorTextMenuSecondary: palette.textSecondary,
          colorTextMenuSelected: palette.primary,
          colorBgMenuItemSelected: alpha(palette.primaryRGB, 0.1),
          colorTextMenuActive: palette.primary,
          colorTextMenuItemHover: palette.primary,
        },
      }}
      // 顶部导航栏样式
      headerTheme="light"
      headerStyle={{
        background: '#FFFFFF',
        borderBottom: '1px solid #E2E8F0',
        paddingInline: 24,
      }}
      // 右上角用户头像
      avatarProps={{
        icon: <UserOutlined style={{ color: palette.primary }} />,
        title: realName || username || '用户',
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
                  label: `角色：${role != null ? ROLE[role]?.label ?? role : '-'}`,
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
        minHeight: 'calc(100vh - 64px)',
        padding: 24,
        background: '#F8FAFC',
      }}
    >
      <Outlet />
    </ProLayout>
  );
}
