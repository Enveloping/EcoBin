import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  PageContainer,
  ProForm,
  ProFormText,
} from '@ant-design/pro-components';
import { App, Card } from 'antd';
import { changeOwnPassword } from '@/api/identityDirectory';
import { changeCurrentPlatformAdministratorPassword } from '@/api/platformAdminAccounts';
import { useAuthStore } from '@/stores/authStore';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { pageHeader } from '@/utils/pageStyle';

interface PasswordForm {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

export default function AccountSettingsPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { session, clear } = useAuthStore();
  const [passwordSubmitting, setPasswordSubmitting] = useState(false);
  const executeCommand = useCommandExecutor();

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
      await executeCommand<unknown>(
        commandKey('change-own-password', session.subjectUid, payload),
        (intent) => session.accountType === 'PLATFORM_ADMIN'
          ? changeCurrentPlatformAdministratorPassword(payload, intent)
          : changeOwnPassword(payload, intent),
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
      <Card title="修改密码" style={{ maxWidth: 720 }}>
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
    </PageContainer>
  );
}
