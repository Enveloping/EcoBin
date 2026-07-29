import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { SafetyCertificateOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Empty,
  Form,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  type TableColumnsType,
} from 'antd';
import {
  changeMembershipStatus,
  createMembership,
  getEffectiveAccess,
  listMemberships,
  replaceMembershipAuthorization,
  replaceTenantPermissions,
  type DirectoryContext,
} from '@/api/identityDirectory';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { palette } from '@/theme';
import type {
  EffectiveAccess,
  IdentityOrganization,
  PermissionDefinition,
  StaffAccount,
  StaffMembership,
} from '@/types';

const { Text } = Typography;
const MEMBERSHIP_PAGE_SIZE = 200;

export interface StaffAccessPanelProps {
  context: DirectoryContext;
  staff: StaffAccount;
  organizations: IdentityOrganization[];
  definitions: PermissionDefinition[];
  canManage: boolean;
  onChanged?: () => void;
}

interface OrganizationAccessRow extends IdentityOrganization {
  membership?: StaffMembership;
}

interface MembershipAuthorizationForm {
  manager: boolean;
  permissionCodes: string[];
}

interface MembershipEditor {
  mode: 'create' | 'authorization' | 'activation';
  organization: IdentityOrganization;
  membership?: StaffMembership;
}

async function listEveryMembership(
  context: DirectoryContext,
  organizationCode: string,
): Promise<StaffMembership[]> {
  const firstPage = await listMemberships(
    context,
    organizationCode,
    1,
    MEMBERSHIP_PAGE_SIZE,
  );
  const effectivePageSize = Math.max(firstPage.pageSize, 1);
  const pageCount = Math.ceil(firstPage.total / effectivePageSize);
  if (pageCount <= 1) return firstPage.items;

  const remainingPages = await Promise.all(
    Array.from({ length: pageCount - 1 }, (_, index) =>
      listMemberships(
        context,
        organizationCode,
        index + 2,
        effectivePageSize,
      )),
  );
  return [
    ...firstPage.items,
    ...remainingPages.flatMap((page) => page.items),
  ];
}

function samePermissionSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const rightSet = new Set(right);
  return left.every((permissionCode) => rightSet.has(permissionCode));
}

export default function StaffAccessPanel({
  context,
  staff,
  organizations,
  definitions,
  canManage,
  onChanged,
}: StaffAccessPanelProps) {
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const requestSequence = useRef(0);
  const [authorizationForm] = Form.useForm<MembershipAuthorizationForm>();
  const manager = Form.useWatch('manager', authorizationForm);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string>();
  const [effectiveAccess, setEffectiveAccess] =
    useState<EffectiveAccess | null>(null);
  const [membershipsByOrganization, setMembershipsByOrganization] = useState<
    Record<string, StaffMembership>
  >({});
  const [tenantPermissionCodes, setTenantPermissionCodes] = useState<string[]>(
    [],
  );
  const [membershipEditor, setMembershipEditor] =
    useState<MembershipEditor | null>(null);
  const [pendingAction, setPendingAction] = useState<string>();

  const tenantPermissionOptions = useMemo(
    () =>
      definitions
        .filter((definition) => definition.scopeKind === 'TENANT')
        .map((definition) => ({
          label: `${definition.permissionName} · ${definition.permissionCode}`,
          value: definition.permissionCode,
        })),
    [definitions],
  );

  const organizationPermissionOptions = useMemo(
    () =>
      definitions
        .filter((definition) => definition.scopeKind === 'ORGANIZATION')
        .map((definition) => ({
          label: `${definition.permissionName} · ${definition.permissionCode}`,
          value: definition.permissionCode,
        })),
    [definitions],
  );

  const permissionNames = useMemo(
    () =>
      new Map(
        definitions.map((definition) => [
          definition.permissionCode,
          definition.permissionName,
        ]),
      ),
    [definitions],
  );

  const loadAccess = useCallback(async () => {
    const requestId = ++requestSequence.current;
    setLoading(true);
    setLoadError(undefined);
    try {
      const [access, organizationMemberships] = await Promise.all([
        getEffectiveAccess(context, staff.staffAccountUid),
        Promise.all(
          organizations.map(async (organization) => ({
            organizationCode: organization.organizationCode,
            memberships: await listEveryMembership(
              context,
              organization.organizationCode,
            ),
          })),
        ),
      ]);
      if (requestId !== requestSequence.current) return;

      const nextMemberships: Record<string, StaffMembership> = {};
      organizationMemberships.forEach(({ organizationCode, memberships }) => {
        const membership = memberships.find(
          (item) => item.staffAccountUid === staff.staffAccountUid,
        );
        if (membership) nextMemberships[organizationCode] = membership;
      });
      setEffectiveAccess(access);
      setTenantPermissionCodes(access.tenantPermissionCodes);
      setMembershipsByOrganization(nextMemberships);
    } catch (error) {
      if (requestId !== requestSequence.current) return;
      setLoadError(
        error instanceof Error ? error.message : '任职与授权加载失败',
      );
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }, [context, organizations, staff.staffAccountUid]);

  useEffect(() => {
    setEffectiveAccess(null);
    setMembershipsByOrganization({});
    setTenantPermissionCodes([]);
    void loadAccess();
    return () => {
      requestSequence.current += 1;
    };
  }, [loadAccess]);

  const organizationRows = useMemo<OrganizationAccessRow[]>(
    () =>
      organizations.map((organization) => ({
        ...organization,
        membership:
          membershipsByOrganization[organization.organizationCode],
      })),
    [membershipsByOrganization, organizations],
  );

  const renderPermissionTags = (permissionCodes: string[]) => {
    if (permissionCodes.length === 0) {
      return <Text type="secondary">无直接权限</Text>;
    }
    return (
      <Space size={[0, 4]} wrap>
        {permissionCodes.map((permissionCode) => (
          <Tooltip key={permissionCode} title={permissionCode}>
            <Tag style={{ marginInlineEnd: 4 }}>
              {permissionNames.get(permissionCode) ?? permissionCode}
            </Tag>
          </Tooltip>
        ))}
      </Space>
    );
  };

  const refreshAfterChange = async () => {
    await loadAccess();
    onChanged?.();
  };

  const saveTenantPermissions = async () => {
    if (!effectiveAccess || staff.accountKind === 'TENANT_PRINCIPAL') return;
    const payload = {
      permissionCodes: tenantPermissionCodes,
      expectedAuthVersion: effectiveAccess.authVersion,
    };
    setPendingAction('tenant-permissions');
    try {
      await executeCommand(
        commandKey(
          'replace-tenant-permissions',
          staff.staffAccountUid,
          payload,
        ),
        (intent) =>
          replaceTenantPermissions(
            context,
            staff.staffAccountUid,
            payload.permissionCodes,
            payload.expectedAuthVersion,
            intent,
          ),
      );
      message.success('租户权限已更新，目标账号的现有会话已撤销');
      await refreshAfterChange();
    } catch {
      // The shared request layer has already shown the actionable API problem.
    } finally {
      setPendingAction(undefined);
    }
  };

  const openMembershipEditor = (
    mode: MembershipEditor['mode'],
    organization: IdentityOrganization,
    membership?: StaffMembership,
  ) => {
    setMembershipEditor({ mode, organization, membership });
  };

  const submitMembershipAuthorization = async () => {
    if (!membershipEditor || !effectiveAccess) return;
    try {
      const values = await authorizationForm.validateFields();
      const permissionCodes = values.manager
        ? []
        : values.permissionCodes ?? [];
      const { organization, membership, mode } = membershipEditor;
      const target = `${organization.organizationCode}:${staff.staffAccountUid}`;
      setPendingAction('membership-editor');

      if (mode === 'create') {
        const payload = {
          staffAccountUid: staff.staffAccountUid,
          manager: values.manager,
          permissionCodes,
          expectedAuthVersion: effectiveAccess.authVersion,
        };
        await executeCommand(
          commandKey('create-membership', target, payload),
          (intent) =>
            createMembership(
              context,
              organization.organizationCode,
              payload,
              intent,
            ),
        );
        message.success('机构任职已建立');
      } else if (mode === 'authorization' && membership) {
        const payload = {
          manager: values.manager,
          permissionCodes,
          expectedVersion: membership.version,
          expectedAuthVersion: membership.authVersion,
        };
        await executeCommand(
          commandKey('replace-membership-authorization', target, payload),
          (intent) =>
            replaceMembershipAuthorization(
              context,
              membership,
              payload.manager,
              payload.permissionCodes,
              intent,
            ),
        );
        message.success('任职授权已完整替换');
      } else if (mode === 'activation' && membership) {
        const payload = {
          manager: values.manager,
          permissionCodes,
          expectedVersion: membership.version,
          expectedAuthVersion: membership.authVersion,
          reason: '重新激活机构任职',
        };
        await executeCommand(
          commandKey('activate-membership', target, payload),
          (intent) =>
            changeMembershipStatus(
              context,
              membership,
              true,
              intent,
              payload.manager,
              payload.permissionCodes,
              payload.reason,
            ),
        );
        message.success('机构任职已重新激活');
      } else {
        return;
      }

      setMembershipEditor(null);
      authorizationForm.resetFields();
      await refreshAfterChange();
    } catch {
      // Validation keeps the dialog open; API errors are shown centrally.
    } finally {
      setPendingAction(undefined);
    }
  };

  const deactivateMembership = async (membership: StaffMembership) => {
    const target = `${membership.organizationCode}:${membership.staffAccountUid}`;
    const payload = {
      expectedVersion: membership.version,
      expectedAuthVersion: membership.authVersion,
      reason: '停用机构任职',
    };
    setPendingAction(`deactivate:${target}`);
    try {
      await executeCommand(
        commandKey('deactivate-membership', target, payload),
        (intent) =>
          changeMembershipStatus(
            context,
            membership,
            false,
            intent,
            false,
            [],
            payload.reason,
          ),
      );
      message.success('任职已停用，负责人标记与机构直接权限已清除');
      await refreshAfterChange();
    } catch {
      // The request layer owns API error presentation.
    } finally {
      setPendingAction(undefined);
    }
  };

  const organizationColumns: TableColumnsType<OrganizationAccessRow> = [
    {
      title: '机构',
      dataIndex: 'organizationName',
      width: 190,
      render: (_, organization) => (
        <div>
          <Text strong>{organization.organizationName}</Text>
          <div style={{ color: palette.textSecondary, fontSize: 12 }}>
            {organization.organizationCode}
            {organization.status === 'DISABLED' && (
              <Tag style={{ marginInlineStart: 8 }}>机构已停用</Tag>
            )}
          </div>
        </div>
      ),
    },
    {
      title: '任职',
      width: 120,
      render: (_, organization) => {
        if (staff.accountKind === 'TENANT_PRINCIPAL') {
          return <Tag color="gold">租户主体全权</Tag>;
        }
        if (!organization.membership) {
          return <Text type="secondary">未任职</Text>;
        }
        return organization.membership.manager
          ? <Tag color="gold">机构负责人</Tag>
          : <Tag>普通任职</Tag>;
      },
    },
    {
      title: '直接权限',
      render: (_, organization) => {
        if (staff.accountKind === 'TENANT_PRINCIPAL') {
          return <Text type="secondary">天然全权</Text>;
        }
        if (!organization.membership) {
          return <Text type="secondary">—</Text>;
        }
        if (organization.membership.manager) {
          return <Text type="secondary">负责人天然全权</Text>;
        }
        return renderPermissionTags(organization.membership.permissionCodes);
      },
    },
    {
      title: '状态',
      width: 88,
      render: (_, organization) => {
        if (staff.accountKind === 'TENANT_PRINCIPAL') {
          return <Tag color="green">有效</Tag>;
        }
        const status = organization.membership?.status;
        return status
          ? (
              <Tag color={status === 'ENABLED' ? 'green' : 'default'}>
                {status === 'ENABLED' ? '有效' : '已停用'}
              </Tag>
            )
          : <Text type="secondary">—</Text>;
      },
    },
    {
      title: '操作',
      width: 178,
      render: (_, organization) => {
        if (staff.accountKind === 'TENANT_PRINCIPAL') {
          return <Text type="secondary">不可削弱</Text>;
        }
        if (!canManage) return <Text type="secondary">只读</Text>;
        const membership = organization.membership;
        if (!membership) {
          return organization.status === 'ENABLED'
            ? (
                <Button
                  type="link"
                  size="small"
                  disabled={!!pendingAction}
                  onClick={() =>
                    openMembershipEditor('create', organization)}
                >
                  建立任职
                </Button>
              )
            : <Text type="secondary">机构已停用</Text>;
        }
        if (membership.status === 'DISABLED') {
          return organization.status === 'ENABLED'
            ? (
                <Button
                  type="link"
                  size="small"
                  disabled={!!pendingAction}
                  onClick={() =>
                    openMembershipEditor(
                      'activation',
                      organization,
                      membership,
                    )}
                >
                  重新激活
                </Button>
              )
            : <Text type="secondary">机构已停用</Text>;
        }
        return (
          <Space size={4}>
            <Button
              type="link"
              size="small"
              disabled={!!pendingAction}
              onClick={() =>
                openMembershipEditor(
                  'authorization',
                  organization,
                  membership,
                )}
            >
              替换授权
            </Button>
            <Popconfirm
              title="确认停用这项机构任职？"
              description="负责人标记和机构直接权限会被清除。"
              okText="停用"
              cancelText="取消"
              onConfirm={() => deactivateMembership(membership)}
            >
              <Button
                type="link"
                danger
                size="small"
                disabled={!!pendingAction}
              >
                停用
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  const tenantPermissionsChanged =
    !!effectiveAccess
    && !samePermissionSet(
      tenantPermissionCodes,
      effectiveAccess.tenantPermissionCodes,
    );

  return (
    <Spin spinning={loading}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {loadError && (
          <Alert
            type="error"
            showIcon
            message="任职与授权加载失败"
            description={loadError}
            action={
              <Button size="small" onClick={() => void loadAccess()}>
                重试
              </Button>
            }
          />
        )}

        {staff.status === 'DISABLED' && (
          <Alert
            type="warning"
            showIcon
            message="当前工作人员账号已停用"
            description="这里仍可预先调整授权，但账号恢复前不能登录或使用这些能力。"
          />
        )}

        <section
          style={{
            border: `1px solid ${palette.border}`,
            borderRadius: 8,
            padding: 16,
          }}
        >
          <Space
            align="start"
            style={{ width: '100%', justifyContent: 'space-between' }}
          >
            <div>
              <Text strong>
                <SafetyCertificateOutlined
                  style={{ color: palette.primary, marginInlineEnd: 8 }}
                />
                租户权限
              </Text>
              <div
                style={{
                  color: palette.textSecondary,
                  fontSize: 12,
                  marginTop: 4,
                }}
              >
                租户级权限按完整集合保存，变更后会撤销目标账号现有会话。
              </div>
            </div>
            {staff.accountKind === 'TENANT_PRINCIPAL' && (
              <Tag color="gold">主体账号天然全权</Tag>
            )}
          </Space>

          {staff.accountKind === 'TENANT_PRINCIPAL' ? (
            <div style={{ marginTop: 12 }}>
              <Text type="secondary">
                租户主体账号的权限来自账号类型，不能通过授权配置削弱。
              </Text>
            </div>
          ) : canManage ? (
            <Space.Compact style={{ width: '100%', marginTop: 12 }}>
              <Select
                aria-label="完整租户权限集合"
                mode="multiple"
                value={tenantPermissionCodes}
                options={tenantPermissionOptions}
                maxTagCount="responsive"
                placeholder="暂无租户级直接权限"
                disabled={!effectiveAccess || !!pendingAction}
                onChange={setTenantPermissionCodes}
                style={{ flex: 1 }}
              />
              <Button
                type="primary"
                loading={pendingAction === 'tenant-permissions'}
                disabled={
                  !effectiveAccess
                  || !tenantPermissionsChanged
                  || (!!pendingAction
                    && pendingAction !== 'tenant-permissions')
                }
                onClick={() => void saveTenantPermissions()}
              >
                保存权限
              </Button>
            </Space.Compact>
          ) : (
            <div style={{ marginTop: 12 }}>
              {effectiveAccess?.tenantPermissionCodes.length
                ? renderPermissionTags(effectiveAccess.tenantPermissionCodes)
                : <Text type="secondary">无租户级直接权限</Text>}
            </div>
          )}
        </section>

        <section>
          <div style={{ marginBottom: 10 }}>
            <Text strong>机构任职</Text>
            <Text
              type="secondary"
              style={{ fontSize: 12, marginInlineStart: 10 }}
            >
              覆盖当前租户的全部机构
            </Text>
          </div>
          <Table<OrganizationAccessRow>
            size="small"
            bordered
            rowKey="organizationCode"
            columns={organizationColumns}
            dataSource={organizationRows}
            pagination={{
              pageSize: 5,
              hideOnSinglePage: true,
              showSizeChanger: false,
            }}
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="当前租户暂无机构"
                />
              ),
            }}
            scroll={{ x: 780 }}
          />
        </section>
      </Space>

      <Modal
        title={
          membershipEditor
            ? `${
                membershipEditor.mode === 'create'
                  ? '建立任职'
                  : membershipEditor.mode === 'activation'
                    ? '重新激活任职'
                    : '替换任职授权'
              } · ${membershipEditor.organization.organizationName}`
            : '机构任职'
        }
        open={!!membershipEditor}
        destroyOnClose
        okText={
          membershipEditor?.mode === 'authorization' ? '替换授权' : '确认'
        }
        cancelText="取消"
        confirmLoading={pendingAction === 'membership-editor'}
        onCancel={() => {
          if (pendingAction !== 'membership-editor') {
            setMembershipEditor(null);
            authorizationForm.resetFields();
          }
        }}
        onOk={() => void submitMembershipAuthorization()}
        afterOpenChange={(open) => {
          if (!open || !membershipEditor) return;
          authorizationForm.setFieldsValue({
            manager: membershipEditor.membership?.manager ?? false,
            permissionCodes:
              membershipEditor.membership?.permissionCodes ?? [],
          });
        }}
      >
        <Alert
          type="info"
          showIcon
          message="授权按完整集合替换"
          description="机构负责人天然拥有本机构全部当前及未来能力；选择负责人时，直接权限会清空。"
          style={{ marginBottom: 16 }}
        />
        <Form<MembershipAuthorizationForm>
          form={authorizationForm}
          layout="vertical"
          preserve={false}
          onValuesChange={(changedValues) => {
            if (changedValues.manager === true) {
              authorizationForm.setFieldValue('permissionCodes', []);
            }
          }}
        >
          <Form.Item
            name="manager"
            label="机构负责人"
            valuePropName="checked"
          >
            <Switch checkedChildren="是" unCheckedChildren="否" />
          </Form.Item>
          <Form.Item name="permissionCodes" label="普通任职的完整权限集合">
            <Select
              mode="multiple"
              options={organizationPermissionOptions}
              maxTagCount="responsive"
              placeholder={manager ? '负责人无需配置直接权限' : '可留空'}
              disabled={manager}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Spin>
  );
}
