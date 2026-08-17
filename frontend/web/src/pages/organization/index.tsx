import { useEffect, useRef, useState } from 'react';
import { DownOutlined, PlusOutlined, PoweroffOutlined } from '@ant-design/icons';
import {
  ModalForm,
  PageContainer,
  ProFormText,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  App,
  Button,
  Divider,
  Dropdown,
  Empty,
  Popconfirm,
  Space,
  Tag,
  Tabs,
} from 'antd';
import { Link } from 'react-router-dom';
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
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { directoryPath } from '@/router/directoryQuery';
import OrganizationMiniappConfiguration from './OrganizationMiniappConfiguration';

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
  const canManageMiniapp = useAuthStore((state) =>
    state.hasCapability('miniapp.manage'));
  const canEdit = canManage
    || canManageMiniapp;
  const [editing, setEditing] = useState<IdentityOrganization | null>(null);
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('profile');
  const executeCommand = useCommandExecutor();

  useEffect(() => {
    actionRef.current?.reload();
  }, [scope.context]);

  const submit = async (values: OrganizationForm) => {
    if (!scope.context) return false;
    if (editing) {
      const payload = {
        organizationName: values.organizationName,
        contactPhone: values.contactPhone,
        contactAddress: values.contactAddress,
        expectedVersion: editing.version,
      };
      await executeCommand(
        commandKey('update-organization', editing.organizationCode, payload),
        (intent) =>
          updateOrganization(
            scope.context!,
            editing.organizationCode,
            payload,
            intent,
          ),
      );
      message.success('机构资料已更新');
    } else {
      await executeCommand(
        commandKey('create-organization', values.organizationCode, values),
        (intent) => createOrganization(scope.context!, values, intent),
      );
      message.success('机构已创建，默认处于停用状态');
    }
    setOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const toggle = async (organization: IdentityOrganization) => {
    if (!scope.context) return;
    const enable = organization.status !== 'ENABLED';
    const reason = enable ? '启用机构' : '停用机构';
    const updated = await executeCommand(
      commandKey('change-organization-status', organization.organizationCode, {
        enable,
        expectedVersion: organization.version,
        reason,
      }),
      (intent) =>
        changeOrganizationStatus(
          scope.context!,
          organization,
          enable,
          intent,
          reason,
        ),
    );
    message.success(enable ? '机构已启用' : '机构已停用');
    setEditing((current) =>
      current?.organizationCode === updated.organizationCode
        ? updated
        : current);
    actionRef.current?.reload();
  };

  const relatedPath = (
    pathname: string,
    organization: IdentityOrganization,
  ) => directoryPath(pathname, {
    tenant: scope.platform ? scope.tenantCode : undefined,
    organization: organization.organizationCode,
  });

  const columns: ProColumns<IdentityOrganization>[] = [
    {
      title: '机构编码',
      dataIndex: 'organizationCode',
      copyable: true,
      width: 180,
      search: false,
    },
    {
      title: '机构名称',
      dataIndex: 'organizationName',
      search: false,
      render: (_, organization) => (
        <Link
          className="table-link"
          to={relatedPath('/organization-users', organization)}
        >
          {organization.organizationName}
        </Link>
      ),
    },
    {
      title: '机构编码 / 名称',
      dataIndex: 'query',
      hideInTable: true,
      hideInSetting: true,
    },
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
    {
      title: '版本',
      dataIndex: 'version',
      search: false,
      width: 80,
    },
    {
      title: '关联数据',
      key: 'relatedData',
      search: false,
      width: 130,
      render: (_, organization) => (
        <Dropdown
          menu={{
            items: [
              {
                key: 'users',
                label: (
                  <Link to={relatedPath('/organization-users', organization)}>
                    机构用户
                  </Link>
                ),
              },
              {
                key: 'devices',
                disabled: !useAuthStore.getState()
                  .hasCapability('device.read'),
                label: (
                  <Link to={relatedPath('/devices', organization)}>
                    设备
                  </Link>
                ),
              },
              {
                key: 'deliveries',
                disabled: !(
                  useAuthStore.getState().hasCapability('delivery.read')
                  || useAuthStore.getState().hasCapability('review.execute')
                ),
                label: (
                  <Link to={relatedPath('/deliveries', organization)}>
                    投递订单
                  </Link>
                ),
              },
              {
                key: 'cleaning',
                disabled: !useAuthStore.getState()
                  .hasCapability('clean.read'),
                label: (
                  <Link to={relatedPath('/clean-operations', organization)}>
                    清运操作
                  </Link>
                ),
              },
              {
                key: 'withdrawals',
                disabled: !(
                  useAuthStore.getState().hasCapability('withdrawal.read')
                  || useAuthStore.getState().hasCapability('review.execute')
                ),
                label: (
                  <Link to={relatedPath('/withdrawals', organization)}>
                    提现订单
                  </Link>
                ),
              },
            ],
          }}
        >
          <Button type="link" size="small">
            查看 <DownOutlined />
          </Button>
        </Dropdown>
      ),
    },
    {
      title: '操作',
      key: 'operation',
      valueType: 'option',
      width: 88,
      hideInTable: !canEdit,
      hideInSetting: true,
      render: (_, organization) => [
        <a
          key="edit"
          onClick={() => {
            setEditing(organization);
            setActiveTab('profile');
            setOpen(true);
          }}
        >
          编辑
        </a>,
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
          columnsState={{
            persistenceKey: 'ecobin.web.columns.organizations.v1',
            persistenceType: 'localStorage',
            defaultValue: {
              version: { show: false },
            },
          }}
          request={async (params) => {
            if (!scope.context) {
              return { data: [], total: 0, success: true };
            }
            try {
              const page = await listOrganizations(scope.context, {
                page: params.current,
                pageSize: params.pageSize,
                status: params.status as string | undefined,
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
                      setActiveTab('profile');
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
        title={editing ? `编辑机构 · ${editing.organizationName}` : '创建机构'}
        open={open}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen);
          if (!nextOpen) setActiveTab('profile');
        }}
        initialValues={editing ?? undefined}
        modalProps={{ destroyOnClose: true, width: editing ? 960 : 760 }}
        submitter={
          activeTab !== 'profile' || (editing && !canManage)
            ? false
            : {
                searchConfig: {
                  submitText: editing ? '保存机构资料' : '创建机构',
                },
              }
        }
        onFinish={submit}
      >
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          destroyInactiveTabPane={false}
          items={[
            {
              key: 'profile',
              label: '基础资料',
              children: (
                <>
                  <ProFormText
                    name="organizationCode"
                    label="机构编码"
                    disabled={!!editing || !canManage}
                    rules={[
                      { required: true },
                      {
                        pattern: /^[a-z0-9][a-z0-9-]*$/,
                        message: '仅允许小写字母、数字和连字符',
                      },
                    ]}
                  />
                  <ProFormText
                    name="organizationName"
                    label="机构名称"
                    disabled={!canManage}
                    rules={[{ required: true }]}
                  />
                  <ProFormText
                    name="contactPhone"
                    label="联系电话"
                    disabled={!canManage}
                  />
                  <ProFormText
                    name="contactAddress"
                    label="联系地址"
                    disabled={!canManage}
                  />
                  {editing && (
                    <>
                      <Divider orientation="left">机构状态</Divider>
                      <Space>
                        <Tag
                          color={
                            editing.status === 'ENABLED' ? 'green' : 'default'
                          }
                        >
                          {editing.status === 'ENABLED' ? '已启用' : '已停用'}
                        </Tag>
                        {canManage && (
                          <Popconfirm
                            title={
                              editing.status === 'ENABLED'
                                ? '停用后该机构会立即从工作人员实时授权中移除，确认继续？'
                                : '确认启用该机构？'
                            }
                            onConfirm={() => toggle(editing)}
                          >
                            <Button
                              danger={editing.status === 'ENABLED'}
                              icon={<PoweroffOutlined />}
                            >
                              {editing.status === 'ENABLED'
                                ? '停用机构'
                                : '启用机构'}
                            </Button>
                          </Popconfirm>
                        )}
                      </Space>
                    </>
                  )}
                </>
              ),
            },
            ...(editing && canManageMiniapp && scope.context
              ? [
                  {
                    key: 'miniapp',
                    label: '小程序登录',
                    children: (
                      <OrganizationMiniappConfiguration
                        active={open && activeTab === 'miniapp'}
                        context={scope.context}
                        organizationCode={editing.organizationCode}
                      />
                    ),
                  },
                ]
              : []),
          ]}
        />
      </ModalForm>
    </PageContainer>
  );
}
