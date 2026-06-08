import { ProLayout } from '@ant-design/pro-components';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Dropdown } from 'antd';
import { LogoutOutlined, UserOutlined } from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';
import { menuRoutesFor } from '@/router/routes';
import { ROLE } from '@/constants';

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
      title="EcoBin 管理后台"
      logo={false}
      layout="mix"
      fixedHeader
      fixSiderbar
      location={{ pathname: location.pathname }}
      route={{ path: '/', routes: menuData }}
      menuItemRender={(item, dom) => (
        <a onClick={() => item.path && navigate(item.path)}>{dom}</a>
      )}
      avatarProps={{
        icon: <UserOutlined />,
        title: realName || username || '用户',
        size: 'small',
        render: (_props, dom) => (
          <Dropdown
            menu={{
              items: [
                {
                  key: 'role',
                  disabled: true,
                  label: `角色：${role != null ? ROLE[role]?.label ?? role : '-'}`,
                },
                { type: 'divider' },
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  onClick: handleLogout,
                },
              ],
            }}
          >
            {dom}
          </Dropdown>
        ),
      }}
    >
      <Outlet />
    </ProLayout>
  );
}
