import { useEffect, useMemo, useRef, useState } from 'react';
import { PlusOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import {
  ModalForm,
  PageContainer,
  ProFormSelect,
  ProFormSwitch,
  ProFormText,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { App, Button, Empty, Popconfirm, Select, Space, Tag } from 'antd';
import {
  changeMembershipStatus,
  createMembership,
  getEffectiveAccess,
  listMemberships,
  listOrganizations,
  listPermissionDefinitions,
  listStaffAccounts,
  provisionOrganizationStaff,
  replaceMembershipAuthorization,
  replaceTenantPermissions,
} from '@/api/identityDirectory';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import type {
  EffectiveAccess,
  IdentityOrganization,
  PermissionDefinition,
  StaffAccount,
  StaffMembership,
} from '@/types';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';

interface TenantPermissionForm {
  permissionCodes: string[];
}

interface MembershipForm {
  staffAccountUid?: string;
  manager: boolean;
  permissionCodes: string[];
}

interface ProvisionForm extends MembershipForm {
  loginName: string;
  initialPassword: string;
  displayName: string;
  contactPhone?: string;
}

export default function AccessPage() {
  const scope = useDirectoryScope();
  const membershipAction = useRef<ActionType>(null);
  const { message } = App.useApp();
  const [staff, setStaff] = useState<StaffAccount[]>([]);
  const [organizations, setOrganizations] =
    useState<IdentityOrganization[]>([]);
  const [definitions, setDefinitions] = useState<PermissionDefinition[]>([]);
  const [organizationCode, setOrganizationCode] = useState<string>();
  const [tenantPermissionTarget, setTenantPermissionTarget] =
    useState<StaffAccount | null>(null);
  const [effectiveAccess, setEffectiveAccess] =
    useState<EffectiveAccess | null>(null);
  const [membershipTarget, setMembershipTarget] =
    useState<StaffMembership | null>(null);
  const [membershipMode, setMembershipMode] =
    useState<'create' | 'authorization' | 'activation' | null>(null);
  const [provisionOpen, setProvisionOpen] = useState(false);

  const loadDirectory = async () => {
    if (!scope.context) {
      setStaff([]);
      setOrganizations([]);
      setDefinitions([]);
      return;
    }
    const [staffPage, organizationPage, permissionDefinitions] =
      await Promise.all([
        listStaffAccounts(scope.context, { page: 1, pageSize: 200 }),
        listOrganizations(scope.context, { page: 1, pageSize: 200 }),
        listPermissionDefinitions(scope.context),
      ]);
    setStaff(staffPage.items);
    setOrganizations(organizationPage.items);
    setDefinitions(permissionDefinitions);
    setOrganizationCode((current) =>
      organizationPage.items.some(
        (organization) => organization.organizationCode === current,
      )
        ? current
        : organizationPage.items[0]?.organizationCode);
  };

  useEffect(() => {
    void loadDirectory().catch(() => undefined);
  }, [scope.context]);

  useEffect(() => {
    membershipAction.current?.reload();
  }, [organizationCode]);

  const tenantOptions = useMemo(
    () =>
      definitions
        .filter((definition) => definition.scopeKind === 'TENANT')
        .map((definition) => ({
          label: `${definition.permissionName} · ${definition.permissionCode}`,
          value: definition.permissionCode,
        })),
    [definitions],
  );
  const organizationOptions = useMemo(
    () =>
      definitions
        .filter((definition) => definition.scopeKind === 'ORGANIZATION')
        .map((definition) => ({
          label: `${definition.permissionName} · ${definition.permissionCode}`,
          value: definition.permissionCode,
        })),
    [definitions],
  );
  const staffOptions = useMemo(
    () =>
      staff
        .filter((account) => account.accountKind === 'STAFF')
        .map((account) => ({
          label: `${account.displayName} · ${account.loginName}`,
          value: account.staffAccountUid,
        })),
    [staff],
  );

  const openTenantPermissions = async (account: StaffAccount) => {
    if (!scope.context) return;
    const access = await getEffectiveAccess(
      scope.context,
      account.staffAccountUid,
    );
    setEffectiveAccess(access);
    setTenantPermissionTarget(account);
  };

  const submitTenantPermissions = async (values: TenantPermissionForm) => {
    if (!scope.context || !tenantPermissionTarget || !effectiveAccess) {
      return false;
    }
    await replaceTenantPermissions(
      scope.context,
      tenantPermissionTarget.staffAccountUid,
      values.permissionCodes ?? [],
      effectiveAccess.authVersion,
    );
    message.success('租户权限已替换，目标账号会话已撤销');
    setTenantPermissionTarget(null);
    setEffectiveAccess(null);
    await loadDirectory();
    return true;
  };

  const submitMembership = async (values: MembershipForm) => {
    if (!scope.context || !organizationCode) return false;
    const permissions = values.manager ? [] : values.permissionCodes ?? [];
    if (membershipMode === 'create') {
      const account = staff.find(
        (item) => item.staffAccountUid === values.staffAccountUid,
      );
      if (!account || !values.staffAccountUid) return false;
      await createMembership(scope.context, organizationCode, {
        staffAccountUid: values.staffAccountUid,
        manager: values.manager,
        permissionCodes: permissions,
        expectedAuthVersion: account.authVersion,
      });
      message.success('机构任职已建立');
    } else if (membershipTarget && membershipMode === 'activation') {
      await changeMembershipStatus(
        scope.context,
        membershipTarget,
        true,
        values.manager,
        permissions,
        '重新激活机构任职',
      );
      message.success('任职已重新激活，旧授权未被静默恢复');
    } else if (membershipTarget) {
      await replaceMembershipAuthorization(
        scope.context,
        membershipTarget,
        values.manager,
        permissions,
      );
      message.success('任职授权已完整替换');
    }
    setMembershipMode(null);
    setMembershipTarget(null);
    membershipAction.current?.reload();
    await loadDirectory();
    return true;
  };

  const deactivate = async (membership: StaffMembership) => {
    if (!scope.context) return;
    await changeMembershipStatus(
      scope.context,
      membership,
      false,
      false,
      [],
      '停用机构任职',
    );
    message.success('任职已停用，负责人标记与机构直接权限已清除');
    membershipAction.current?.reload();
    await loadDirectory();
  };

  const submitProvision = async (values: ProvisionForm) => {
    if (!scope.context || !organizationCode) return false;
    await provisionOrganizationStaff(
      scope.context,
      organizationCode,
      {
        loginName: values.loginName,
        initialPassword: values.initialPassword,
        displayName: values.displayName,
        contactPhone: values.contactPhone,
        manager: values.manager,
        permissionCodes: values.manager ? [] : values.permissionCodes ?? [],
      },
    );
    message.success('工作人员账号和本机构任职已原子建立');
    setProvisionOpen(false);
    membershipAction.current?.reload();
    await loadDirectory();
    return true;
  };

  const staffColumns: ProColumns<StaffAccount>[] = [
    {
      title: '工作人员',
      render: (_, account) => (
        <div>
          <div>{account.displayName}</div>
          <div style={{ color: '#64748B', fontSize: 12 }}>
            {account.loginName}
          </div>
        </div>
      ),
    },
    {
      title: '账号类型',
      dataIndex: 'accountKind',
      render: (_, account) =>
        account.accountKind === 'TENANT_PRINCIPAL'
          ? <Tag color="gold">天然全权主体</Tag>
          : <Tag>工作人员</Tag>,
    },
    { title: 'authVersion', dataIndex: 'authVersion', width: 120 },
    {
      title: '租户授权',
      valueType: 'option',
      render: (_, account) =>
        account.accountKind === 'TENANT_PRINCIPAL'
          ? [<span key="natural" style={{ color: '#94A3B8' }}>不可削弱</span>]
          : [
              <a key="configure" onClick={() => openTenantPermissions(account)}>
                配置租户权限
              </a>,
            ],
    },
  ];

  const membershipColumns: ProColumns<StaffMembership>[] = [
    { title: '工作人员', dataIndex: 'displayName' },
    {
      title: '负责人',
      dataIndex: 'manager',
      render: (_, membership) =>
        membership.manager ? <Tag color="gold">机构负责人</Tag> : <Tag>普通任职</Tag>,
    },
    {
      title: '直接权限',
      dataIndex: 'permissionCodes',
      render: (_, membership) =>
        membership.manager
          ? '天然全权'
          : membership.permissionCodes.map((code) => <Tag key={code}>{code}</Tag>),
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: (_, membership) => (
        <Tag color={membership.status === 'ENABLED' ? 'green' : 'default'}>
          {membership.status === 'ENABLED' ? '有效' : '已停用'}
        </Tag>
      ),
    },
    {
      title: '版本',
      render: (_, membership) =>
        `v${membership.version} / auth ${membership.authVersion}`,
    },
    {
      title: '操作',
      valueType: 'option',
      render: (_, membership) =>
        membership.status === 'ENABLED'
          ? [
              <a
                key="authorization"
                onClick={() => {
                  setMembershipTarget(membership);
                  setMembershipMode('authorization');
                }}
              >
                替换授权
              </a>,
              <Popconfirm
                key="deactivate"
                title="停用将清除负责人标记与机构直接权限，确认继续？"
                onConfirm={() => deactivate(membership)}
              >
                <a>停用任职</a>
              </Popconfirm>,
            ]
          : [
              <a
                key="activate"
                onClick={() => {
                  setMembershipTarget(membership);
                  setMembershipMode('activation');
                }}
              >
                重新任职
              </a>,
            ],
    },
  ];

  return (
    <PageContainer
      {...pageHeader(
        '任职与授权',
        '不创建角色模板；租户权限和机构任职授权都以完整集合原子替换。',
      )}
    >
      <DirectoryScopeBar scope={scope} />
      {!scope.context && !scope.loading ? (
        <Empty description="请选择目标租户" />
      ) : (
        <Space direction="vertical" size={24} style={{ width: '100%' }}>
          <ProTable<StaffAccount>
            {...proTableConfig}
            headerTitle="租户权限"
            rowKey="staffAccountUid"
            search={false}
            pagination={{ pageSize: 8 }}
            columns={staffColumns}
            dataSource={staff}
          />
          <div>
            <Space style={{ marginBottom: 16 }}>
              <strong>机构任职</strong>
              <Select
                style={{ width: 320 }}
                value={organizationCode}
                placeholder="选择机构"
                options={organizations.map((organization) => ({
                  label: organization.organizationName,
                  value: organization.organizationCode,
                }))}
                onChange={setOrganizationCode}
              />
            </Space>
            <ProTable<StaffMembership>
              {...proTableConfig}
              actionRef={membershipAction}
              rowKey="staffAccountUid"
              search={false}
              columns={membershipColumns}
              request={async (params) => {
                if (!scope.context || !organizationCode) {
                  return { data: [], total: 0, success: true };
                }
                try {
                  const page = await listMemberships(
                    scope.context,
                    organizationCode,
                    params.current,
                    params.pageSize,
                  );
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
                  disabled={!organizationCode}
                  onClick={() => {
                    setMembershipTarget(null);
                    setMembershipMode('create');
                  }}
                >
                  建立任职
                </Button>,
                <Button
                  key="provision"
                  disabled={!organizationCode}
                  onClick={() => setProvisionOpen(true)}
                >
                  创建账号并任职
                </Button>,
              ]}
            />
          </div>
        </Space>
      )}

      <ModalForm<TenantPermissionForm>
        title={`租户权限 · ${tenantPermissionTarget?.displayName ?? ''}`}
        open={!!tenantPermissionTarget}
        onOpenChange={(open) => {
          if (!open) {
            setTenantPermissionTarget(null);
            setEffectiveAccess(null);
          }
        }}
        initialValues={{
          permissionCodes: effectiveAccess?.tenantPermissionCodes ?? [],
        }}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitTenantPermissions}
      >
        <ProFormSelect
          name="permissionCodes"
          label="完整租户权限集合"
          mode="multiple"
          options={tenantOptions}
          fieldProps={{ prefix: <SafetyCertificateOutlined /> }}
        />
      </ModalForm>

      <ModalForm<MembershipForm>
        title={
          membershipMode === 'create'
            ? '建立机构任职'
            : membershipMode === 'activation'
              ? '重新激活任职'
              : '替换任职授权'
        }
        open={!!membershipMode}
        onOpenChange={(open) => {
          if (!open) {
            setMembershipMode(null);
            setMembershipTarget(null);
          }
        }}
        initialValues={{
          staffAccountUid: membershipTarget?.staffAccountUid,
          manager: membershipTarget?.manager ?? false,
          permissionCodes: membershipTarget?.permissionCodes ?? [],
        }}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitMembership}
      >
        {membershipMode === 'create' && (
          <ProFormSelect
            name="staffAccountUid"
            label="工作人员"
            options={staffOptions}
            rules={[{ required: true }]}
          />
        )}
        <ProFormSwitch
          name="manager"
          label="机构负责人"
          tooltip="负责人天然拥有本机构全部当前及未来能力，负责人模式下直接权限会被清空。"
        />
        <ProFormSelect
          name="permissionCodes"
          label="普通任职的完整权限集合"
          mode="multiple"
          options={organizationOptions}
        />
      </ModalForm>

      <ModalForm<ProvisionForm>
        title="创建工作人员并原子建立本机构任职"
        open={provisionOpen}
        onOpenChange={setProvisionOpen}
        initialValues={{ manager: false, permissionCodes: [] }}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitProvision}
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
          rules={[{ required: true }, { min: 8 }]}
        />
        <ProFormText
          name="displayName"
          label="展示名"
          rules={[{ required: true }]}
        />
        <ProFormText name="contactPhone" label="联系电话" />
        <ProFormSwitch name="manager" label="机构负责人" />
        <ProFormSelect
          name="permissionCodes"
          label="普通任职的完整权限集合"
          mode="multiple"
          options={organizationOptions}
        />
      </ModalForm>
    </PageContainer>
  );
}
