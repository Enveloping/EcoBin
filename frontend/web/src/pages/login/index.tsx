import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Card, Form, Input, Button, Segmented, Typography, App } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { login } from '@/api/auth';
import { useAuthStore } from '@/stores/authStore';
import { defaultPathFor } from '@/router/routes';
import { loginGradient, palette } from '@/theme';
import ParticleBackground from '@/components/ParticleBackground';
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
        background: `linear-gradient(135deg, ${loginGradient.from} 0%, ${loginGradient.via} 50%, ${loginGradient.to} 100%)`,
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* 粒子动效背景 */}
      <ParticleBackground color="255, 255, 255" density={0.1} />

      {/* 背景装饰 - 环保元素 */}
      <div
        style={{
          position: 'absolute',
          top: '10%',
          left: '5%',
          width: 200,
          height: 200,
          borderRadius: '50%',
          background: 'rgba(255,255,255,0.08)',
          filter: 'blur(40px)',
          zIndex: 0,
        }}
      />
      <div
        style={{
          position: 'absolute',
          bottom: '15%',
          right: '10%',
          width: 300,
          height: 300,
          borderRadius: '50%',
          background: 'rgba(255,255,255,0.06)',
          filter: 'blur(60px)',
          zIndex: 0,
        }}
      />

      {/* Logo 区域 */}
      <div
        style={{
          position: 'absolute',
          top: 40,
          left: 40,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          zIndex: 2,
        }}
      >
        <svg width="36" height="36" viewBox="0 0 48 48" fill="none">
          <circle cx="24" cy="24" r="22" fill="#FFFFFF" fillOpacity="0.2" />
          <path d="M24 8C15.16 8 8 15.16 8 24C8 32.84 15.16 40 24 40C32.84 40 40 32.84 40 24" stroke="#FFFFFF" strokeWidth="3" strokeLinecap="round" />
          <path d="M32 16L40 24L32 32" stroke="#FFFFFF" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M24 24L24 8" stroke="#FFFFFF" strokeWidth="3" strokeLinecap="round" />
        </svg>
        <div>
          <div style={{ fontSize: 24, fontWeight: 600, color: '#FFFFFF' }}>EcoBin</div>
          <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.7)' }}>智慧环保回收箱</div>
        </div>
      </div>

      {/* 登录卡片 */}
      <Card
        style={{
          width: 400,
          boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
          borderRadius: 16,
          backdropFilter: 'blur(10px)',
          background: 'rgba(255, 255, 255, 0.95)',
          position: 'relative',
          zIndex: 2,
        }}
        styles={{
          body: { padding: 32 },
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Title level={3} style={{ marginBottom: 8, color: palette.textPrimary }}>
            管理后台登录
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
          style={{ marginBottom: 24 }}
        />

        <Form onFinish={onFinish} size="large" initialValues={{ username: '', password: '' }}>
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input
              prefix={<UserOutlined style={{ color: '#94A3B8' }} />}
              placeholder="用户名"
              autoComplete="username"
            />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              prefix={<LockOutlined style={{ color: '#94A3B8' }} />}
              placeholder="密码"
              autoComplete="current-password"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button
              type="primary"
              htmlType="submit"
              block
              loading={loading}
              style={{
                height: 44,
                fontWeight: 500,
              }}
            >
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
