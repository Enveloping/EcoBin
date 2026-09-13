import { useRef, useState } from 'react';
import {
  ApartmentOutlined,
  KeyOutlined,
  PlusOutlined,
  PoweroffOutlined,
} from '@ant-design/icons';
import {
  ModalForm,
  PageContainer,
  ProFormText,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { App, Button, Divider, Popconfirm, Space, Tag } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import {
  changeTenantStatus,
  createIdentityTenant,
  createTenantPrincipal,
  getIdentityTenant,
  listIdentityTenants,
  resetTenantPrincipalPassword,
  updateIdentityTenant,
} from '@/api/identityDirectory';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import type { IdentityTenant } from '@/types';
import { useAuthStore } from '@/stores/authStore';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { palette } from '@/theme';
import { directoryPath } from '@/router/directoryQuery';

interface TenantForm {
  tenantCode: string;
  enterpriseName: string;
  contactName?: string;
  contactPhone?: string;
  contactAddress?: string;
}

interface PrincipalForm {
  loginName: string;
  initialPassword: string;
  displayName: string;
  contactPhone?: string;
}

interface PasswordResetForm {
  newPassword: string;
  confirmPassword: string;
}

export default function TenantPage() {
  const [searchParams] = useSearchParams();
  const view = searchParams.get('view') === 'disabled' ? 'disabled' : 'all';
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const [editing, setEditing] = useState<IdentityTenant | null>(null);
  const [principalTenant, setPrincipalTenant] =
    useState<IdentityTenant | null>(null);
  const [passwordTenant, setPasswordTenant] =
    useState<IdentityTenant | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const canManage = useAuthStore((state) =>
    state.hasCapability('tenant.manage'));
  const executeCommand = useCommandExecutor();

  const reload = () => actionRef.current?.reload();

  const submitTenant = async (values: TenantForm) => {
    if (editing) {
      const payload = {
        enterpriseName: values.enterpriseName,
        contactName: values.contactName,
        contactPhone: values.contactPhone,
        contactAddress: values.contactAddress,
        expectedVersion: editing.version,
      };
      await executeCommand(
        commandKey('update-tenant', editing.tenantCode, payload),
        (intent) => updateIdentityTenant(editing.tenantCode, payload, intent),
      );
      message.success('租户资料已更新');
    } else {
      const created = await executeCommand(
        commandKey('create-tenant', values.tenantCode, values),
        (intent) => createIdentityTenant(values, intent),
      );
      message.success('租户已创建');
      setPrincipalTenant(created);
    }
    setFormOpen(false);
    reload();
    return true;
  };

  const submitPrincipal = async (values: PrincipalForm) => {
    if (!principalTenant) return false;
    const payload = {
      ...values,
      expectedVersion: principalTenant.version,
    };
    await executeCommand(
      commandKey('create-tenant-principal', principalTenant.tenantCode, payload),
      (intent) =>
        createTenantPrincipal(principalTenant.tenantCode, payload, intent),
    );
    message.success('主体账号已建立，可启用租户');
    const latest = await getIdentityTenant(principalTenant.tenantCode);
    setEditing((current) =>
      current?.tenantCode === latest.tenantCode ? latest : current);
    setPrincipalTenant(null);
    reload();
    return true;
  };

  const toggle = async (tenant: IdentityTenant) => {
    const enable = tenant.status !== 'ENABLED';
    const reason = enable ? '平台启用租户' : '平台停用租户';
    const updated = await executeCommand(
      commandKey('change-tenant-status', tenant.tenantCode, {
        enable,
        expectedVersion: tenant.version,
        reason,
      }),
      (intent) =>
        changeTenantStatus(
          tenant.tenantCode,
          enable,
          tenant.version,
          intent,
          reason,
        ),
    );
    message.success(enable ? '租户已启用' : '租户已停用，活动会话已撤销');
    setEditing((current) =>
      current?.tenantCode === updated.tenantCode ? updated : current);
    reload();
  };

  const submitPasswordReset = async (values: PasswordResetForm) => {
    if (!passwordTenant?.principalAccount) return false;
    if (values.newPassword !== values.confirmPassword) {
      message.error('两次输入的新密码不一致');
      return false;
    }
    const principal = passwordTenant.principalAccount;
    await executeCommand(
      commandKey('reset-tenant-principal-password', passwordTenant.tenantCode, {
        newPassword: values.newPassword,
        expectedVersion: principal.version,
        expectedAuthVersion: principal.authVersion,
      }),
      (intent) =>
        resetTenantPrincipalPassword(
          passwordTenant.tenantCode,
          principal,
          values.newPassword,
          intent,
        ),
    );
    message.success('主体密码已重置，原有主体会话已撤销');
    const latest = await getIdentityTenant(passwordTenant.tenantCode);
    setEditing((current) =>
      current?.tenantCode === latest.tenantCode ? latest : current);
    setPasswordTenant(null);
    reload();
    return true;
  };

  const columns: ProColumns<IdentityTenant>[] = [
    {
      title: '编码',
      dataIndex: 'tenantCode',
      copyable: true,
      width: 180,
      search: false,
    },
    {
      title: '名称',
      dataIndex: 'enterpriseName',
      search: false,
      render: (_, tenant) => (
        <Link
          className="table-link"
          to={directoryPath('/organizations', {
            tenant: tenant.tenantCode,
          })}
        >
          {tenant.enterpriseName}
        </Link>
      ),
    },
    {
      title: '编码 / 名称',
      dataIndex: 'query',
      hideInTable: true,
      hideInSetting: true,
    },
    { title: '联系人', dataIndex: 'contactName', search: false },
    { title: '联系电话', dataIndex: 'contactPhone', search: false },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      valueType: 'select',
      valueEnum: {
        ENABLED: { text: '已启用', status: 'Success' },
        DISABLED: { text: '已停用', status: 'Default' },
      },
      render: (_, tenant) => (
        <Tag color={tenant.status === 'ENABLED' ? 'green' : 'default'}>
          {tenant.status === 'ENABLED' ? '已启用' : '已停用'}
        </Tag>
      ),
    },
    {
      title: '主体账号',
      key: 'principalAccount',
      search: false,
      render: (_, tenant) =>
        tenant.principalAccount ? (
          <Space direction="vertical" size={0}>
            <span>{tenant.principalAccount.staffAccountUid}</span>
            <span style={{ color: palette.textSecondary, fontSize: 12 }}>
              authVersion {tenant.principalAccount.authVersion}
            </span>
          </Space>
        ) : (
          <Tag color="orange">待建立</Tag>
        ),
    },
    {
      title: '操作',
      key: 'operation',
      valueType: 'option',
      width: 200,
      hideInTable: !canManage,
      hideInSetting: true,
      render: (_, tenant) => canManage ? [
        <a
          key="edit"
          onClick={() => {
            setEditing(tenant);
            setFormOpen(true);
          }}
        >
          编辑
        </a>,
        !tenant.principalAccount && (
          <a key="principal" onClick={() => setPrincipalTenant(tenant)}>建立主体账号</a>
        ),
        tenant.principalAccount && tenant.status === 'DISABLED' && (
          <Popconfirm key="enable" title={`确认启用 ${tenant.enterpriseName}？`} onConfirm={() => toggle(tenant)}>
            <a>启用租户</a>
          </Popconfirm>
        ),
      ].filter(Boolean) : [],
    },
  ];

  return (
    <PageContainer
      {...pageHeader('租户管理')}
    >
      <ProTable<IdentityTenant>
        {...proTableConfig}
        actionRef={actionRef}
        rowKey="tenantCode"
        columns={columns}
        params={{ view }}
        columnsState={{
          persistenceKey: 'ecobin.web.columns.tenants.v1',
          persistenceType: 'localStorage',
          defaultValue: {
            principalAccount: { show: false },
          },
        }}
        request={async (params) => {
          try {
            const page = await listIdentityTenants({
              page: params.current,
              pageSize: params.pageSize,
              status: view === 'disabled'
                ? 'DISABLED'
                : params.status as string | undefined,
              query: params.query as string | undefined,
            });
            return {
              data: page.items,
              total: page.total,
              success: true,
            };
          } catch {
            return { data: [], total: 0, success: false };
          }
        }}
        toolBarRender={() =>
          canManage
            ? [
                <Button
                  key="create"
                  type="primary"
                  icon={<PlusOutlined />}
                  onClick={() => {
                    setEditing(null);
                    setFormOpen(true);
                  }}
                >
                  创建租户
                </Button>,
              ]
            : []}
      />

      <ModalForm<TenantForm>
        title={editing ? '编辑租户资料' : '创建租户'}
        submitter={{ searchConfig: { submitText: editing ? '保存资料' : '创建租户' } }}
        open={formOpen}
        onOpenChange={setFormOpen}
        initialValues={editing ?? undefined}
        modalProps={{ destroyOnClose: true, width: 680 }}
        onFinish={submitTenant}
      >
        <ProFormText
          name="tenantCode"
          label="租户编码"
          disabled={!!editing}
          fieldProps={{ prefix: <ApartmentOutlined /> }}
          rules={[
            { required: true },
            { pattern: /^[a-z0-9][a-z0-9-]*$/, message: '仅允许小写字母、数字和连字符' },
          ]}
        />
        <ProFormText
          name="enterpriseName"
          label="企业名称"
          rules={[{ required: true }]}
        />
        <ProFormText name="contactName" label="联系人" />
        <ProFormText name="contactPhone" label="联系电话" />
        <ProFormText name="contactAddress" label="联系地址" />
        {editing && (
          <>
            <Divider orientation="left">主体账号与租户状态</Divider>
            <Space wrap>
              {!editing.principalAccount ? (
                <Button
                  onClick={() => setPrincipalTenant(editing)}
                >
                  建立主体账号
                </Button>
              ) : (
                <Button
                  icon={<KeyOutlined />}
                  onClick={() => setPasswordTenant(editing)}
                >
                  重置主体密码
                </Button>
              )}
              <Popconfirm
                title={
                  editing.status === 'ENABLED'
                    ? '停用后该租户工作人员会话将立即失效，确认继续？'
                    : '确认启用该租户？'
                }
                onConfirm={() => toggle(editing)}
              >
                <Button
                  danger={editing.status === 'ENABLED'}
                  icon={<PoweroffOutlined />}
                >
                  {editing.status === 'ENABLED' ? '停用租户' : '启用租户'}
                </Button>
              </Popconfirm>
            </Space>
          </>
        )}
      </ModalForm>

      <ModalForm<PrincipalForm>
        title={`建立主体账号 · ${principalTenant?.enterpriseName ?? ''}`}
        submitter={{ searchConfig: { submitText: '建立主体账号' } }}
        open={!!principalTenant}
        onOpenChange={(open) => !open && setPrincipalTenant(null)}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitPrincipal}
      >
        <ProFormText
          name="loginName"
          label="全局登录名"
          rules={[
            { required: true },
            { pattern: /^[a-z0-9][a-z0-9._-]*$/, message: '请输入规范化小写登录名' },
          ]}
        />
        <ProFormText.Password
          name="initialPassword"
          label="初始密码"
          fieldProps={{ prefix: <KeyOutlined /> }}
          rules={[{ required: true }, { min: 8 }]}
        />
        <ProFormText
          name="displayName"
          label="展示名"
          rules={[{ required: true }]}
        />
        <ProFormText name="contactPhone" label="联系电话" />
        <div style={{ color: palette.textSecondary }}>
          <PoweroffOutlined /> 主体账号创建后仍需在租户列表中显式启用租户。
        </div>
      </ModalForm>

      <ModalForm<PasswordResetForm>
        title={`重置主体密码 · ${passwordTenant?.enterpriseName ?? ''}`}
        open={!!passwordTenant}
        onOpenChange={(open) => !open && setPasswordTenant(null)}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitPasswordReset}
      >
        <ProFormText.Password
          name="newPassword"
          label="新密码"
          fieldProps={{ prefix: <KeyOutlined /> }}
          rules={[{ required: true }, { min: 8 }]}
        />
        <ProFormText.Password
          name="confirmPassword"
          label="确认新密码"
          rules={[{ required: true }, { min: 8 }]}
        />
        <div style={{ color: palette.textSecondary }}>
          保存后将立即撤销该主体账号的全部活动会话。
        </div>
      </ModalForm>
    </PageContainer>
  );
}
