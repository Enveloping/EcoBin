import { useEffect, useRef, useState } from 'react';
import {
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
import {
  App,
  Button,
  Divider,
  Empty,
  Form,
  Popconfirm,
  Space,
  Tag,
} from 'antd';
import {
  changeStaffStatus,
  createStaffAccount,
  getCurrentEffectiveAccess,
  getStaffAccount,
  listAllOrganizations,
  listPermissionDefinitions,
  listStaffAccounts,
  resetStaffPassword,
  updateStaffAccount,
} from '@/api/identityDirectory';
import { useAuthStore } from '@/stores/authStore';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import type {
  EffectiveAccess,
  PermissionDefinition,
  StaffAccount,
} from '@/types';
import type { IdentityOrganization } from '@/types';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { palette } from '@/theme';
import StaffAccessPanel from './StaffAccessPanel';
import PermissionTreeSelector from './PermissionTreeSelector';

interface StaffForm {
  loginName: string;
  initialPassword: string;
  displayName: string;
  contactPhone?: string;
  permissionCodes: string[];
}

interface PasswordForm {
  newPassword: string;
  confirmPassword: string;
}

export default function StaffPage() {
  const scope = useDirectoryScope();
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const canManage = useAuthStore((state) =>
    state.hasCapability('staff.manage'));
  const canGrant = useAuthStore((state) =>
    state.hasCapability('permission.manage'));
  const canReadAccess = useAuthStore((state) =>
    state.hasCapability('permission.read'));
  const session = useAuthStore((state) => state.session);
  const [definitions, setDefinitions] = useState<PermissionDefinition[]>([]);
  const [organizations, setOrganizations] =
    useState<IdentityOrganization[]>([]);
  const [editing, setEditing] = useState<StaffAccount | null>(null);
  const [passwordTarget, setPasswordTarget] =
    useState<StaffAccount | null>(null);
  const [open, setOpen] = useState(false);
  const [operatorAccess, setOperatorAccess] =
    useState<EffectiveAccess | null>(null);
  const executeCommand = useCommandExecutor();
  const canViewAccess = canReadAccess || canGrant;
  const operatorNeedsDelegationLimit = session?.accountType === 'STAFF';

  useEffect(() => {
    actionRef.current?.reload();
    if (!scope.context || !canViewAccess) {
      setDefinitions([]);
      setOrganizations([]);
      setOperatorAccess(null);
      return;
    }
    Promise.all([
      listPermissionDefinitions(scope.context),
      listAllOrganizations(scope.context),
      operatorNeedsDelegationLimit && canGrant
        ? getCurrentEffectiveAccess()
        : Promise.resolve(null),
    ])
      .then(([nextDefinitions, nextOrganizations, nextOperatorAccess]) => {
        setDefinitions(nextDefinitions);
        setOrganizations(nextOrganizations);
        setOperatorAccess(nextOperatorAccess);
      })
      .catch(() => {
        setDefinitions([]);
        setOrganizations([]);
        setOperatorAccess(null);
      });
  }, [
    canGrant,
    canViewAccess,
    operatorNeedsDelegationLimit,
    scope.context,
  ]);

  const delegableTenantPermissionCodes = operatorNeedsDelegationLimit
    ? operatorAccess?.tenantPermissionCodes ?? []
    : undefined;

  const submit = async (values: StaffForm) => {
    if (!scope.context) return false;
    if (editing) {
      if (!canManage || editing.accountKind === 'TENANT_PRINCIPAL') {
        setOpen(false);
        return true;
      }
      const payload = {
        displayName: values.displayName,
        contactPhone: values.contactPhone,
        expectedVersion: editing.version,
      };
      await executeCommand(
        commandKey('update-staff', editing.staffAccountUid, payload),
        (intent) =>
          updateStaffAccount(
            scope.context!,
            editing.staffAccountUid,
            payload,
            intent,
          ),
      );
      message.success('工作人员资料已更新');
    } else {
      const payload = {
        loginName: values.loginName,
        initialPassword: values.initialPassword,
        displayName: values.displayName,
        contactPhone: values.contactPhone,
        permissionCodes: values.permissionCodes ?? [],
      };
      await executeCommand(
        commandKey('create-staff', values.loginName, payload),
        (intent) => createStaffAccount(scope.context!, payload, intent),
      );
      message.success('工作人员账号已创建');
    }
    setOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const toggle = async (staff: StaffAccount) => {
    if (!scope.context) return;
    const enable = staff.status !== 'ENABLED';
    const reason = enable ? '恢复工作人员账号' : '停用工作人员账号';
    const updated = await executeCommand(
      commandKey('change-staff-status', staff.staffAccountUid, {
        enable,
        expectedVersion: staff.version,
        expectedAuthVersion: staff.authVersion,
        reason,
      }),
      (intent) =>
        changeStaffStatus(scope.context!, staff, enable, intent, reason),
    );
    message.success(enable ? '账号已恢复' : '账号已停用，会话已撤销');
    setEditing((current) =>
      current?.staffAccountUid === updated.staffAccountUid
        ? updated
        : current);
    actionRef.current?.reload();
  };

  const refreshEditing = async (staffUid: string) => {
    if (!scope.context) return;
    const latest = await getStaffAccount(scope.context, staffUid);
    setEditing((current) =>
      current?.staffAccountUid === latest.staffAccountUid
        ? latest
        : current);
    actionRef.current?.reload();
  };

  const submitPassword = async (values: PasswordForm) => {
    if (!scope.context || !passwordTarget) return false;
    if (values.newPassword !== values.confirmPassword) {
      message.error('两次输入的密码不一致');
      return false;
    }
    const updated = await executeCommand(
      commandKey('reset-staff-password', passwordTarget.staffAccountUid, {
        newPassword: values.newPassword,
        expectedVersion: passwordTarget.version,
        expectedAuthVersion: passwordTarget.authVersion,
      }),
      (intent) =>
        resetStaffPassword(
          scope.context!,
          passwordTarget,
          values.newPassword,
          intent,
        ),
    );
    message.success('密码已重置，目标账号的会话已撤销');
    setEditing((current) =>
      current?.staffAccountUid === updated.staffAccountUid
        ? updated
        : current);
    setPasswordTarget(null);
    actionRef.current?.reload();
    return true;
  };

  const columns: ProColumns<StaffAccount>[] = [
    {
      title: '工作人员',
      dataIndex: 'displayName',
      render: (_, staff) => (
        <div>
          <div>{staff.displayName}</div>
          <div style={{ color: palette.textSecondary, fontSize: 12 }}>
            {staff.loginName}
          </div>
        </div>
      ),
    },
    {
      title: '账号类型',
      dataIndex: 'accountKind',
      search: false,
      render: (_, staff) =>
        staff.accountKind === 'TENANT_PRINCIPAL'
          ? <Tag color="gold">租户主体</Tag>
          : <Tag>工作人员</Tag>,
    },
    { title: '联系电话', dataIndex: 'contactPhone', search: false },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      valueType: 'select',
      valueEnum: {
        ENABLED: { text: '已启用' },
        DISABLED: { text: '已停用' },
      },
      render: (_, staff) => (
        <Tag color={staff.status === 'ENABLED' ? 'green' : 'default'}>
          {staff.status === 'ENABLED' ? '已启用' : '已停用'}
        </Tag>
      ),
    },
    {
      title: '安全版本',
      key: 'securityVersion',
      search: false,
      width: 120,
      render: (_, staff) => `v${staff.version} / auth ${staff.authVersion}`,
    },
    {
      title: '公开标识',
      dataIndex: 'staffAccountUid',
      copyable: true,
      search: false,
      ellipsis: true,
    },
    {
      title: '操作',
      key: 'operation',
      valueType: 'option',
      width: 88,
      hideInTable: !canManage && !canReadAccess,
      hideInSetting: true,
      render: (_, staff) => [
              <a
                key="edit"
                onClick={() => {
                  setEditing(staff);
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
      {...pageHeader(
        '工作人员',
        '登录名全平台唯一；账号状态与安全版本由服务端实时校验。',
      )}
    >
      <DirectoryScopeBar scope={scope} />
      {!scope.context && !scope.loading ? (
        <Empty description="请选择目标租户" />
      ) : (
        <ProTable<StaffAccount>
          {...proTableConfig}
          actionRef={actionRef}
          rowKey="staffAccountUid"
          columns={columns}
          columnsState={{
            persistenceKey: 'ecobin.web.columns.staff.v1',
            persistenceType: 'localStorage',
            defaultValue: {
              securityVersion: { show: false },
              staffAccountUid: { show: false },
            },
          }}
          request={async (params) => {
            if (!scope.context) {
              return { data: [], total: 0, success: true };
            }
            try {
              const page = await listStaffAccounts(scope.context, {
                page: params.current,
                pageSize: params.pageSize,
                status: params.status as string | undefined,
                query: params.displayName as string | undefined,
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
                    创建工作人员
                  </Button>,
                ]
              : []
          }
        />
      )}

      <ModalForm<StaffForm>
        title={editing ? '编辑工作人员资料' : '创建工作人员'}
        open={open}
        onOpenChange={setOpen}
        initialValues={editing ?? { permissionCodes: [] }}
        modalProps={{
          destroyOnClose: true,
          width: editing ? 1080 : 760,
          style: { top: editing ? 24 : undefined },
          styles: editing
            ? { body: { maxHeight: 'calc(100dvh - 180px)', overflowY: 'auto' } }
            : undefined,
        }}
        submitter={{
          searchConfig: {
            submitText:
              editing
              && (!canManage || editing.accountKind === 'TENANT_PRINCIPAL')
                ? '关闭'
                : editing
                  ? '保存资料'
                  : '创建',
            resetText: '取消',
          },
        }}
        onFinish={submit}
      >
        <ProFormText
          name="loginName"
          label="全局登录名"
          disabled={!!editing || !canManage}
          rules={
            editing
              ? []
              : [
                  { required: true },
                  { pattern: /^[a-z0-9][a-z0-9._-]*$/, message: '请输入规范化小写登录名' },
                ]
          }
        />
        {!editing && (
          <ProFormText.Password
            name="initialPassword"
            label="初始密码"
            rules={[{ required: true }, { min: 8 }]}
          />
        )}
        <ProFormText
          name="displayName"
          label="展示名"
          disabled={
            !!editing
            && (!canManage || editing.accountKind === 'TENANT_PRINCIPAL')
          }
          rules={[{ required: true }]}
        />
        <ProFormText
          name="contactPhone"
          label="联系电话"
          disabled={
            !!editing
            && (!canManage || editing.accountKind === 'TENANT_PRINCIPAL')
          }
        />
        {!editing && canGrant && (
          <Form.Item
            name="permissionCodes"
            label="初始租户权限"
            tooltip="只能授予当前操作者在相同或更大作用域拥有的权限。"
          >
            <PermissionTreeSelector
              ariaLabel="初始租户权限"
              definitions={definitions}
              scopeKind="TENANT"
              delegablePermissionCodes={delegableTenantPermissionCodes}
            />
          </Form.Item>
        )}
        {editing && (
          <>
            <Divider orientation="left">账号安全</Divider>
            {editing.accountKind === 'TENANT_PRINCIPAL' ? (
              <Tag color="gold">租户主体账号受保护</Tag>
            ) : canManage ? (
              <Space wrap>
                <Button
                  icon={<KeyOutlined />}
                  onClick={() => setPasswordTarget(editing)}
                >
                  重置密码
                </Button>
                <Popconfirm
                  title={
                    editing.status === 'ENABLED'
                      ? '停用后该账号的全部工作人员会话立即失效，确认继续？'
                      : '确认恢复该账号？'
                  }
                  onConfirm={() => toggle(editing)}
                >
                  <Button
                    danger={editing.status === 'ENABLED'}
                    icon={<PoweroffOutlined />}
                  >
                    {editing.status === 'ENABLED' ? '停用账号' : '恢复账号'}
                  </Button>
                </Popconfirm>
              </Space>
            ) : (
              <Tag>账号安全信息只读</Tag>
            )}

            {canViewAccess && scope.context && (
              <>
                <Divider orientation="left">任职与授权</Divider>
                <StaffAccessPanel
                  context={scope.context}
                  staff={editing}
                  organizations={organizations}
                  definitions={definitions}
                  canManage={canGrant}
                  operatorAccess={operatorAccess}
                  operatorNeedsDelegationLimit={operatorNeedsDelegationLimit}
                  onChanged={() =>
                    void refreshEditing(editing.staffAccountUid)}
                />
              </>
            )}
          </>
        )}
      </ModalForm>

      <ModalForm<PasswordForm>
        title={`重置密码 · ${passwordTarget?.displayName ?? ''}`}
        open={!!passwordTarget}
        onOpenChange={(value) => !value && setPasswordTarget(null)}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitPassword}
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
      </ModalForm>
    </PageContainer>
  );
}
