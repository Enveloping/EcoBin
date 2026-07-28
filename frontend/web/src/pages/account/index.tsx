import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  PageContainer,
  ProForm,
  ProFormText,
} from '@ant-design/pro-components';
import { App, Card, Col, Row } from 'antd';
import { changeOwnPassword, updateOwnProfile } from '@/api/identityDirectory';
import { getCurrentSession } from '@/api/auth';
import { useAuthStore } from '@/stores/authStore';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { pageHeader } from '@/utils/pageStyle';

interface ProfileForm {
  displayName: string;
  contactPhone?: string;
}

interface PasswordForm {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

export default function AccountSettingsPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { session, setSession, clear } = useAuthStore();
  const [profileSubmitting, setProfileSubmitting] = useState(false);
  const [passwordSubmitting, setPasswordSubmitting] = useState(false);
  const executeCommand = useCommandExecutor();

  const submitProfile = async (values: ProfileForm) => {
    if (!session) return false;
    setProfileSubmitting(true);
    try {
      const payload = { ...values, expectedVersion: session.version };
      await executeCommand(
        commandKey('update-own-profile', session.subjectUid, payload),
        (intent) => updateOwnProfile(payload, intent),
      );
      const current = await getCurrentSession('tenant');
      setSession(current, 'tenant');
      message.success('个人资料已更新');
      return true;
    } finally {
      setProfileSubmitting(false);
    }
  };

  const submitPassword = async (values: PasswordForm) => {
    if (!session) return false;
    if (values.newPassword !== values.confirmPassword) {
      message.error('两次输入的新密码不一致');
      return false;
    }
    setPasswordSubmitting(true);
    try {
      const payload = {
        currentPassword: values.currentPassword,
        newPassword: values.newPassword,
        expectedVersion: session.version,
        expectedAuthVersion: session.authVersion,
      };
      await executeCommand(
        commandKey('change-own-password', session.subjectUid, payload),
        (intent) => changeOwnPassword(payload, intent),
      );
      message.success('密码已修改，请重新登录');
      clear();
      navigate('/login', { replace: true });
      return true;
    } finally {
      setPasswordSubmitting(false);
    }
  };

  return (
    <PageContainer
      {...pageHeader(
        '账号设置',
        '登录名和账号类型不可修改；改密会立即撤销当前会话。',
      )}
    >
      <Row gutter={[24, 24]}>
        <Col xs={24} lg={12}>
          <Card title="个人资料">
            <ProForm<ProfileForm>
              initialValues={{
                displayName: session?.displayName,
                contactPhone: session?.contactPhone ?? '',
              }}
              submitter={{ submitButtonProps: { loading: profileSubmitting } }}
              onFinish={submitProfile}
            >
              <ProFormText
                name="displayName"
                label="展示名"
                rules={[{ required: true }]}
              />
              <ProFormText name="contactPhone" label="联系电话" />
            </ProForm>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="修改密码">
            <ProForm<PasswordForm>
              submitter={{ submitButtonProps: { loading: passwordSubmitting } }}
              onFinish={submitPassword}
            >
              <ProFormText.Password
                name="currentPassword"
                label="当前密码"
                rules={[{ required: true }]}
              />
              <ProFormText.Password
                name="newPassword"
                label="新密码"
                rules={[{ required: true }, { min: 8 }]}
              />
              <ProFormText.Password
                name="confirmPassword"
                label="确认新密码"
                rules={[{ required: true }, { min: 8 }]}
              />
            </ProForm>
          </Card>
        </Col>
      </Row>
    </PageContainer>
  );
}
