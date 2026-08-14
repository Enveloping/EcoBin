import { useRef, useState } from 'react';
import { DeleteOutlined, KeyOutlined, PlusOutlined } from '@ant-design/icons';
import {
  ModalForm,
  PageContainer,
  ProFormText,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { App, Button, Popconfirm, Tag } from 'antd';
import {
  changePlatformAdministratorStatus,
  createPlatformAdministrator,
  deletePlatformAdministrator,
  listPlatformAdministrators,
  resetPlatformAdministratorPassword,
} from '@/api/platformAdminAccounts';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import type { PlatformAdmin } from '@/types';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import { palette } from '@/theme';

interface CreateForm {
  loginName: string;
  initialPassword: string;
  confirmPassword: string;
  displayName: string;
}

interface PasswordResetForm {
  newPassword: string;
  confirmPassword: string;
}

interface DeleteForm {
  confirmLoginName: string;
  reason: string;
}

const statusText: Record<PlatformAdmin['status'], string> = {
  ACTIVE: '正常',
  DISABLED: '已停用',
  DELETED: '已永久删除',
};

export default function PlatformAdministratorsPage() {
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const [createOpen, setCreateOpen] = useState(false);
  const [passwordTarget, setPasswordTarget] = useState<PlatformAdmin | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PlatformAdmin | null>(null);
  const reload = () => actionRef.current?.reload();

  const submitCreate = async (values: CreateForm) => {
    if (values.initialPassword !== values.confirmPassword) {
      message.error('两次输入的初始密码不一致');
      return false;
    }
    const payload = {
      loginName: values.loginName,
      initialPassword: values.initialPassword,
      displayName: values.displayName,
    };
    await executeCommand(
      commandKey('create-platform-admin', values.loginName, payload),
      (intent) => createPlatformAdministrator(payload, intent),
    );
    message.success('普通平台管理员已创建，可以立即登录');
    setCreateOpen(false);
    reload();
    return true;
  };

  const toggleStatus = async (administrator: PlatformAdmin) => {
    const enabled = administrator.status !== 'ACTIVE';
    const reason = enabled
      ? '默认平台管理员启用普通平台管理员'
      : '默认平台管理员停用普通平台管理员';
    const payload = {
      enabled,
      expectedVersion: administrator.version,
      expectedAuthVersion: administrator.authVersion,
      reason,
    };
    await executeCommand(
      commandKey(
        'change-platform-admin-status',
        administrator.platformAdminUid,
        payload,
      ),
      (intent) => changePlatformAdministratorStatus(
        administrator,
        enabled,
        reason,
        intent,
      ),
    );
    message.success(
      enabled ? '管理员已启用' : '管理员已停用，已有登录会话已撤销',
    );
    reload();
  };

  const submitPasswordReset = async (values: PasswordResetForm) => {
    if (!passwordTarget) return false;
    if (values.newPassword !== values.confirmPassword) {
      message.error('两次输入的新密码不一致');
      return false;
    }
    const payload = {
      newPassword: values.newPassword,
      expectedVersion: passwordTarget.version,
      expectedAuthVersion: passwordTarget.authVersion,
    };
    await executeCommand(
      commandKey(
        'reset-platform-admin-password',
        passwordTarget.platformAdminUid,
        payload,
      ),
      (intent) => resetPlatformAdministratorPassword(
        passwordTarget,
        values.newPassword,
        intent,
      ),
    );
    message.success('密码已重置，该管理员的已有登录会话已撤销');
    setPasswordTarget(null);
    reload();
    return true;
  };

  const submitDelete = async (values: DeleteForm) => {
    if (!deleteTarget) return false;
    if (values.confirmLoginName.trim() !== deleteTarget.loginName) {
      message.error('输入的登录名与待删除账号不一致');
      return false;
    }
    const payload = {
      expectedVersion: deleteTarget.version,
      expectedAuthVersion: deleteTarget.authVersion,
      reason: values.reason,
    };
    await executeCommand(
      commandKey(
        'delete-platform-admin',
        deleteTarget.platformAdminUid,
        payload,
      ),
      (intent) => deletePlatformAdministrator(
        deleteTarget,
        values.reason,
        intent,
      ),
    );
    message.success('管理员已永久逻辑删除，不能恢复或重新登录');
    setDeleteTarget(null);
    reload();
    return true;
  };

  const columns: ProColumns<PlatformAdmin>[] = [
    {
      title: '登录名',
      dataIndex: 'loginName',
      copyable: true,
      search: false,
    },
    {
      title: '登录名 / 展示名',
      dataIndex: 'query',
      hideInTable: true,
      hideInSetting: true,
    },
    { title: '展示名', dataIndex: 'displayName', search: false },
    {
      title: '账号类型',
      dataIndex: 'adminKind',
      width: 130,
      search: false,
      render: (_, administrator) => administrator.adminKind === 'DEFAULT'
        ? <Tag color='blue'>默认管理员</Tag>
        : <Tag>普通管理员</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      valueType: 'select',
      valueEnum: {
        ACTIVE: { text: statusText.ACTIVE, status: 'Success' },
        DISABLED: { text: statusText.DISABLED, status: 'Default' },
        DELETED: { text: statusText.DELETED, status: 'Error' },
      },
      render: (_, administrator) => (
        <Tag color={
          administrator.status === 'ACTIVE'
            ? 'green'
            : administrator.status === 'DELETED' ? 'red' : 'default'
        }>
          {statusText[administrator.status]}
        </Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      valueType: 'dateTime',
      search: false,
      width: 180,
    },
    {
      title: '操作',
      key: 'operation',
      valueType: 'option',
      width: 250,
      hideInSetting: true,
      render: (_, administrator) => {
        if (administrator.adminKind === 'DEFAULT') {
          return [
            <span key='protected' style={{ color: palette.textSecondary }}>
              受保护账号
            </span>,
          ];
        }
        if (administrator.status === 'DELETED') {
          return [
            <span key='deleted' style={{ color: palette.textSecondary }}>
              不可恢复
            </span>,
          ];
        }
        return [
          <a key='password' onClick={() => setPasswordTarget(administrator)}>
            重置密码
          </a>,
          <Popconfirm
            key='status'
            title={administrator.status === 'ACTIVE'
              ? '停用后该管理员的已有登录会话会立即失效，确认继续？'
              : '确认重新启用该管理员？'}
            onConfirm={() => toggleStatus(administrator)}
          >
            <a>{administrator.status === 'ACTIVE' ? '停用' : '启用'}</a>
          </Popconfirm>,
          <a
            key='delete'
            style={{ color: palette.error }}
            onClick={() => setDeleteTarget(administrator)}
          >
            永久删除
          </a>,
        ];
      },
    },
  ];

  return (
    <PageContainer
      {...pageHeader(
        '平台管理员',
        '仅默认平台管理员可治理其他管理员；普通管理员只能修改自己的密码。',
      )}
    >
      <ProTable<PlatformAdmin>
        {...proTableConfig}
        actionRef={actionRef}
        rowKey='platformAdminUid'
        columns={columns}
        columnsState={{
          persistenceKey: 'ecobin.web.columns.platform-admins.v1',
          persistenceType: 'localStorage',
        }}
        request={async (params) => {
          try {
            const page = await listPlatformAdministrators({
              page: params.current,
              pageSize: params.pageSize,
              status: params.status as PlatformAdmin['status'] | undefined,
              query: params.query as string | undefined,
            });
            return { data: page.items, total: page.total, success: true };
          } catch {
            return { data: [], total: 0, success: false };
          }
        }}
        toolBarRender={() => [
          <Button
            key='create'
            type='primary'
            icon={<PlusOutlined />}
            onClick={() => setCreateOpen(true)}
          >
            创建管理员
          </Button>,
        ]}
      />

      <ModalForm<CreateForm>
        title='创建普通平台管理员'
        open={createOpen}
        onOpenChange={setCreateOpen}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitCreate}
      >
        <ProFormText
          name='loginName'
          label='登录名'
          rules={[
            { required: true },
            {
              pattern: /^[A-Za-z0-9][A-Za-z0-9._-]*$/,
              message: '仅允许字母、数字、点、下划线和连字符',
            },
          ]}
        />
        <ProFormText
          name='displayName'
          label='展示名'
          rules={[{ required: true }]}
        />
        <ProFormText.Password
          name='initialPassword'
          label='初始密码'
          fieldProps={{ prefix: <KeyOutlined /> }}
          rules={[{ required: true }, { min: 8 }]}
        />
        <ProFormText.Password
          name='confirmPassword'
          label='确认初始密码'
          rules={[{ required: true }, { min: 8 }]}
        />
      </ModalForm>

      <ModalForm<PasswordResetForm>
        title={`重置密码 · ${passwordTarget?.loginName ?? ''}`}
        open={!!passwordTarget}
        onOpenChange={(open) => !open && setPasswordTarget(null)}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitPasswordReset}
      >
        <ProFormText.Password
          name='newPassword'
          label='新密码'
          fieldProps={{ prefix: <KeyOutlined /> }}
          rules={[{ required: true }, { min: 8 }]}
        />
        <ProFormText.Password
          name='confirmPassword'
          label='确认新密码'
          rules={[{ required: true }, { min: 8 }]}
        />
        <div style={{ color: palette.textSecondary }}>
          保存后将立即撤销该管理员的全部活动会话。
        </div>
      </ModalForm>

      <ModalForm<DeleteForm>
        title={`永久删除管理员 · ${deleteTarget?.loginName ?? ''}`}
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        modalProps={{ destroyOnClose: true }}
        submitter={{
          searchConfig: { submitText: '确认永久删除' },
          submitButtonProps: { danger: true, icon: <DeleteOutlined /> },
        }}
        onFinish={submitDelete}
      >
        <div style={{ color: palette.error, marginBottom: 16 }}>
          删除后账号不能恢复、不能登录，登录名也不能再次使用。
        </div>
        <ProFormText
          name='confirmLoginName'
          label={`请输入登录名 ${deleteTarget?.loginName ?? ''} 以确认`}
          rules={[{ required: true }]}
        />
        <ProFormText
          name='reason'
          label='删除原因'
          rules={[{ required: true }, { max: 500 }]}
        />
      </ModalForm>
    </PageContainer>
  );
}
