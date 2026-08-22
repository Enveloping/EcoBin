import { useState } from 'react';
import { Navigate, useNavigate, useLocation } from 'react-router-dom';
import { Card, Form, Input, Button, Segmented, Typography, App } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { login } from '@/api/auth';
import { useAuthStore } from '@/stores/authStore';
import { defaultPathFor } from '@/router/routes';
import { palette } from '@/theme';
import EcoBinLogo from '@/components/Logo';
import ComplianceFooter from '@/components/ComplianceFooter';
import type { WebLoginDomain } from '@/types';
import {
  parseWebLoginDomain,
  preferredLoginDomain,
  rememberLoginDomain,
} from '@/security/loginDomain';

const { Title, Text } = Typography;

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = App.useApp();
  const setSession = useAuthStore((state) => state.setSession);
  const status = useAuthStore((state) => state.status);
  const currentSession = useAuthStore((state) => state.session);
  const requestedDomain = parseWebLoginDomain(
    new URLSearchParams(location.search).get('domain'),
  );
  const [domain, setDomain] = useState<WebLoginDomain>(
    () => requestedDomain ?? preferredLoginDomain(),
  );
  const [loading, setLoading] = useState(false);

  const from = (location.state as { from?: unknown } | null)?.from;
  const safeFrom = typeof from === 'string'
    && from.startsWith('/')
    && !from.startsWith('//')
    ? from
    : undefined;

  if (status === 'authenticated' && currentSession) {
    return (
      <Navigate
        to={safeFrom || defaultPathFor(currentSession)}
        replace
      />
    );
  }

  const onFinish = async (values: { loginName: string; password: string }) => {
    setLoading(true);
    try {
      const session = await login(domain, values);
      setSession(session, domain);
      message.success('登录成功');
      navigate(safeFrom || defaultPathFor(session), { replace: true });
    } catch {
      // 错误已由拦截器统一弹窗
    } finally {
      setLoading(false);
    }
  };

  const selectDomain = (value: WebLoginDomain) => {
    setDomain(value);
    rememberLoginDomain(value);
  };

  return (
    <div
      className="login-page"
      style={{
        minHeight: '100dvh',
        display: 'grid',
        gridTemplateRows: 'minmax(0, 1fr) auto',
        justifyItems: 'center',
        padding: '72px 16px 0',
        boxSizing: 'border-box',
        background: palette.bgLayout,
        position: 'relative',
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: 20,
          left: 0,
        }}
      >
        <EcoBinLogo />
      </div>

      <Card
        className="login-card"
        style={{
          width: '100%',
          maxWidth: 400,
          alignSelf: 'center',
          border: `1px solid ${palette.border}`,
          boxShadow: '0 8px 24px rgba(15, 23, 42, 0.06)',
          borderRadius: 12,
          background: palette.bgContainer,
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

        <Segmented<WebLoginDomain>
          block
          value={domain}
          onChange={selectDomain}
          options={[
            { label: '租户与工作人员', value: 'tenant' },
            { label: '平台管理员', value: 'platform' },
          ]}
          style={{ marginBottom: 24 }}
        />

        <Form onFinish={onFinish} size="large" initialValues={{ loginName: '', password: '' }}>
          <Form.Item name="loginName" rules={[{ required: true, message: '请输入登录名' }]}>
            <Input
              prefix={<UserOutlined style={{ color: palette.textSecondary }} />}
              placeholder="登录名"
              autoComplete="username"
            />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              prefix={<LockOutlined style={{ color: palette.textSecondary }} />}
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
      <ComplianceFooter />
    </div>
  );
}
