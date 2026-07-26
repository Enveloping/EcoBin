import { useEffect, useRef, useState } from 'react';
import { PlusOutlined } from '@ant-design/icons';
import {
  ModalForm,
  PageContainer,
  ProFormText,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { App, Button, Empty, Popconfirm, Tag } from 'antd';
import {
  changeOrganizationStatus,
  createOrganization,
  listOrganizations,
  updateOrganization,
} from '@/api/identityDirectory';
import { useAuthStore } from '@/stores/authStore';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import type { IdentityOrganization } from '@/types';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';

interface OrganizationForm {
  organizationCode: string;
  organizationName: string;
  contactPhone?: string;
  contactAddress?: string;
}

export default function OrganizationPage() {
  const scope = useDirectoryScope();
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const canManage = useAuthStore((state) =>
    state.hasCapability('organization.manage'));
  const [editing, setEditing] = useState<IdentityOrganization | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    actionRef.current?.reload();
  }, [scope.context]);

  const submit = async (values: OrganizationForm) => {
    if (!scope.context) return false;
    if (editing) {
      await updateOrganization(
        scope.context,
        editing.organizationCode,
        {
          organizationName: values.organizationName,
          contactPhone: values.contactPhone,
          contactAddress: values.contactAddress,
          expectedVersion: editing.version,
        },
      );
      message.success('机构资料已更新');
    } else {
      await createOrganization(scope.context, values);
      message.success('机构已创建，默认处于停用状态');
    }
    setOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const toggle = async (organization: IdentityOrganization) => {
    if (!scope.context) return;
    const enable = organization.status !== 'ENABLED';
    await changeOrganizationStatus(
      scope.context,
      organization,
      enable,
      enable ? '启用机构' : '停用机构',
    );
    message.success(enable ? '机构已启用' : '机构已停用');
    actionRef.current?.reload();
  };

  const columns: ProColumns<IdentityOrganization>[] = [
    {
      title: '机构编码',
      dataIndex: 'organizationCode',
      copyable: true,
      width: 180,
    },
    { title: '机构名称', dataIndex: 'organizationName' },
    { title: '联系电话', dataIndex: 'contactPhone', search: false },
    { title: '联系地址', dataIndex: 'contactAddress', search: false },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      valueType: 'select',
      valueEnum: {
        ENABLED: { text: '已启用' },
        DISABLED: { text: '已停用' },
      },
      render: (_, organization) => (
        <Tag color={organization.status === 'ENABLED' ? 'green' : 'default'}>
          {organization.status === 'ENABLED' ? '已启用' : '已停用'}
        </Tag>
      ),
    },
    { title: '版本', dataIndex: 'version', search: false, width: 80 },
    {
      title: '操作',
      valueType: 'option',
      width: 150,
      hideInTable: !canManage,
      render: (_, organization) => [
        <a
          key="edit"
          onClick={() => {
            setEditing(organization);
            setOpen(true);
          }}
        >
          编辑
        </a>,
        <Popconfirm
          key="status"
          title={
            organization.status === 'ENABLED'
              ? '停用后该机构会立即从工作人员实时授权中移除，确认继续？'
              : '确认启用该机构？'
          }
          onConfirm={() => toggle(organization)}
        >
          <a>{organization.status === 'ENABLED' ? '停用' : '启用'}</a>
        </Popconfirm>,
      ],
    },
  ];

  return (
    <PageContainer
      {...pageHeader('机构管理', '机构作用域由服务端会话和任职实时判定。')}
    >
      <DirectoryScopeBar scope={scope} />
      {!scope.context && !scope.loading ? (
        <Empty description="请选择目标租户" />
      ) : (
        <ProTable<IdentityOrganization>
          {...proTableConfig}
          actionRef={actionRef}
          rowKey="organizationCode"
          columns={columns}
          request={async (params) => {
            if (!scope.context) {
              return { data: [], total: 0, success: true };
            }
            try {
              const page = await listOrganizations(scope.context, {
                page: params.current,
                pageSize: params.pageSize,
                status: params.status as string | undefined,
                query: params.organizationName as string | undefined,
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
                      setOpen(true);
                    }}
                  >
                    创建机构
                  </Button>,
                ]
              : []
          }
        />
      )}

      <ModalForm<OrganizationForm>
        title={editing ? '编辑机构资料' : '创建机构'}
        open={open}
        onOpenChange={setOpen}
        initialValues={editing ?? undefined}
        modalProps={{ destroyOnClose: true }}
        onFinish={submit}
      >
        <ProFormText
          name="organizationCode"
          label="机构编码"
          disabled={!!editing}
          rules={[
            { required: true },
            { pattern: /^[a-z0-9][a-z0-9-]*$/, message: '仅允许小写字母、数字和连字符' },
          ]}
        />
        <ProFormText
          name="organizationName"
          label="机构名称"
          rules={[{ required: true }]}
        />
        <ProFormText name="contactPhone" label="联系电话" />
        <ProFormText name="contactAddress" label="联系地址" />
      </ModalForm>
    </PageContainer>
  );
}
