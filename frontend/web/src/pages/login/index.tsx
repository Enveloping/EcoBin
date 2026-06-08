import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Card, Form, Input, Button, Segmented, Typography, App } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { login } from '@/api/auth';
import { useAuthStore } from '@/stores/authStore';
import { defaultPathFor } from '@/router/routes';
import type { UserType } from '@/types';

const { Title, Text } = Typography;

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = App.useApp();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [userType, setUserType] = useState<UserType>('admin');
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = await login({ userType, ...values });
      setAuth(res);
      message.success('登录成功');
      const from = (location.state as { from?: string })?.from;
      navigate(from || defaultPathFor(res.role), { replace: true });
    } catch {
      // 错误已由拦截器统一弹窗
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #e6f4ff 0%, #f6ffed 100%)',
      }}
    >
      <Card style={{ width: 380, boxShadow: '0 8px 24px rgba(0,0,0,0.08)' }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Title level={3} style={{ marginBottom: 4 }}>
            EcoBin 管理后台
          </Title>
          <Text type="secondary">智慧环保回收箱管理系统</Text>
        </div>

        <Segmented<UserType>
          block
          value={userType}
          onChange={setUserType}
          options={[
            { label: '平台管理员', value: 'admin' },
            { label: '租户', value: 'tenant' },
          ]}
          style={{ marginBottom: 20 }}
        />

        <Form onFinish={onFinish} size="large" initialValues={{ username: '', password: '' }}>
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="密码"
              autoComplete="current-password"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" block loading={loading}>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
