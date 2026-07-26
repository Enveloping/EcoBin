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
import { App, Button, Popconfirm, Space, Tag } from 'antd';
import {
  changeTenantStatus,
  createIdentityTenant,
  createTenantPrincipal,
  listIdentityTenants,
  updateIdentityTenant,
} from '@/api/identityDirectory';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import type { IdentityTenant } from '@/types';

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

export default function TenantPage() {
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const [editing, setEditing] = useState<IdentityTenant | null>(null);
  const [principalTenant, setPrincipalTenant] =
    useState<IdentityTenant | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const reload = () => actionRef.current?.reload();

  const submitTenant = async (values: TenantForm) => {
    if (editing) {
      await updateIdentityTenant(editing.tenantCode, {
        enterpriseName: values.enterpriseName,
        contactName: values.contactName,
        contactPhone: values.contactPhone,
        contactAddress: values.contactAddress,
        expectedVersion: editing.version,
      });
      message.success('租户资料已更新');
    } else {
      await createIdentityTenant(values);
      message.success('租户已创建，下一步请建立主体账号');
    }
    setFormOpen(false);
    reload();
    return true;
  };

  const submitPrincipal = async (values: PrincipalForm) => {
    if (!principalTenant) return false;
    await createTenantPrincipal(principalTenant.tenantCode, {
      ...values,
      expectedVersion: principalTenant.version,
    });
    message.success('主体账号已建立，可启用租户');
    setPrincipalTenant(null);
    reload();
    return true;
  };

  const toggle = async (tenant: IdentityTenant) => {
    const enable = tenant.status !== 'ENABLED';
    await changeTenantStatus(
      tenant.tenantCode,
      enable,
      tenant.version,
      enable ? '平台启用租户' : '平台停用租户',
    );
    message.success(enable ? '租户已启用' : '租户已停用，活动会话已撤销');
    reload();
  };

  const columns: ProColumns<IdentityTenant>[] = [
    {
      title: '租户编码',
      dataIndex: 'tenantCode',
      copyable: true,
      width: 180,
    },
    { title: '企业名称', dataIndex: 'enterpriseName' },
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
      search: false,
      render: (_, tenant) =>
        tenant.principalAccount ? (
          <Space direction="vertical" size={0}>
            <span>{tenant.principalAccount.staffAccountUid}</span>
            <span style={{ color: '#64748B', fontSize: 12 }}>
              authVersion {tenant.principalAccount.authVersion}
            </span>
          </Space>
        ) : (
          <Tag color="orange">待建立</Tag>
        ),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 230,
      render: (_, tenant) => [
        <a
          key="edit"
          onClick={() => {
            setEditing(tenant);
            setFormOpen(true);
          }}
        >
          编辑资料
        </a>,
        !tenant.principalAccount ? (
          <a key="principal" onClick={() => setPrincipalTenant(tenant)}>
            建立主体
          </a>
        ) : null,
        <Popconfirm
          key="status"
          title={
            tenant.status === 'ENABLED'
              ? '停用后该租户工作人员会话将立即失效，确认继续？'
              : '确认启用该租户？'
          }
          onConfirm={() => toggle(tenant)}
        >
          <a>
            {tenant.status === 'ENABLED' ? '停用' : '启用'}
          </a>
        </Popconfirm>,
      ],
    },
  ];

  return (
    <PageContainer
      {...pageHeader(
        '租户管理',
        '平台特权入口；租户先创建、再建立唯一主体账号，最后启用。',
      )}
    >
      <ProTable<IdentityTenant>
        {...proTableConfig}
        actionRef={actionRef}
        rowKey="tenantCode"
        columns={columns}
        request={async (params) => {
          try {
            const page = await listIdentityTenants({
              page: params.current,
              pageSize: params.pageSize,
              status: params.status as string | undefined,
              query: params.enterpriseName as string | undefined,
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
        toolBarRender={() => [
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
        ]}
      />

      <ModalForm<TenantForm>
        title={editing ? '编辑租户资料' : '创建租户'}
        open={formOpen}
        onOpenChange={setFormOpen}
        initialValues={editing ?? undefined}
        modalProps={{ destroyOnClose: true }}
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
      </ModalForm>

      <ModalForm<PrincipalForm>
        title={`建立主体账号 · ${principalTenant?.enterpriseName ?? ''}`}
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
        <div style={{ color: '#64748B' }}>
          <PoweroffOutlined /> 主体账号创建后仍需在租户列表中显式启用租户。
        </div>
      </ModalForm>
    </PageContainer>
  );
}
