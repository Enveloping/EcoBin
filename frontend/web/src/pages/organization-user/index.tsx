import { useEffect, useRef, useState } from 'react';
import {
  CheckCircleOutlined,
  EyeOutlined,
  StopOutlined,
  ToolOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import {
  PageContainer,
  ProDescriptions,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  App,
  Avatar,
  Button,
  Drawer,
  Empty,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import {
  changeOrganizationUserCleanOperation,
  changeOrganizationUserStatus,
  getOrganizationUser,
  listOrganizations,
  listOrganizationUsers,
} from '@/api/identityDirectory';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useAuthStore } from '@/stores/authStore';
import type { OrganizationUser } from '@/types';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';

type UserMutation = 'freeze' | 'restore' | 'grant-clean' | 'revoke-clean';

function userInitial(user: OrganizationUser): string {
  return user.nickname.trim().slice(0, 1).toUpperCase() || '用';
}

export default function OrganizationUserPage() {
  const scope = useDirectoryScope();
  const { message } = App.useApp();
  const actionRef = useRef<ActionType>(null);
  const executeCommand = useCommandExecutor();
  const canFreeze = useAuthStore((state) =>
    state.hasCapability('user.freeze'));
  const canManageCleaner = useAuthStore((state) =>
    state.hasCapability('cleaner.manage'));
  const [organizationCode, setOrganizationCode] = useState<string>();
  const [organizationOptions, setOrganizationOptions] = useState<
    Array<{ label: string; value: string }>
  >([]);
  const [detail, setDetail] = useState<OrganizationUser | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [mutating, setMutating] = useState<UserMutation | null>(null);

  useEffect(() => {
    setOrganizationCode(undefined);
    setOrganizationOptions([]);
    setDetail(null);
    if (!scope.context) return;
    let active = true;
    listOrganizations(scope.context, { page: 1, pageSize: 200 })
      .then((page) => {
        if (!active) return;
        const options = page.items.map((organization) => ({
          label: `${organization.organizationName} · ${organization.organizationCode}`,
          value: organization.organizationCode,
        }));
        setOrganizationOptions(options);
        setOrganizationCode(options[0]?.value);
      })
      .catch(() => {
        if (active) setOrganizationOptions([]);
      });
    return () => {
      active = false;
    };
  }, [scope.context]);

  useEffect(() => {
    actionRef.current?.reload();
    setDetail(null);
  }, [organizationCode]);

  const openDetail = async (user: OrganizationUser) => {
    if (!scope.context || !organizationCode) return;
    setDetail(user);
    setDetailLoading(true);
    try {
      setDetail(
        await getOrganizationUser(
          scope.context,
          organizationCode,
          user.organizationUserUid,
        ),
      );
    } finally {
      setDetailLoading(false);
    }
  };

  const refreshUser = async (user: OrganizationUser) => {
    if (!scope.context || !organizationCode) return;
    const latest = await getOrganizationUser(
      scope.context,
      organizationCode,
      user.organizationUserUid,
    );
    if (detail?.organizationUserUid === latest.organizationUserUid) {
      setDetail(latest);
    }
    actionRef.current?.reload();
  };

  const mutate = async (user: OrganizationUser, mutation: UserMutation) => {
    if (!scope.context || !organizationCode) return;
    const isStatus = mutation === 'freeze' || mutation === 'restore';
    const enabled = mutation === 'restore' || mutation === 'grant-clean';
    const reason = {
      freeze: 'Web 管理端冻结机构用户',
      restore: 'Web 管理端恢复机构用户',
      'grant-clean': 'Web 管理端授予清运能力',
      'revoke-clean': 'Web 管理端撤销清运能力',
    }[mutation];
    setMutating(mutation);
    try {
      const updated = await executeCommand(
        commandKey(mutation, user.organizationUserUid, {
          expectedVersion: user.version,
          expectedAuthVersion: user.authVersion,
          reason,
        }),
        (intent) =>
          isStatus
            ? changeOrganizationUserStatus(
                scope.context!,
                organizationCode,
                user,
                enabled,
                intent,
                reason,
              )
            : changeOrganizationUserCleanOperation(
                scope.context!,
                organizationCode,
                user,
                enabled,
                intent,
                reason,
              ),
      );
      setDetail((current) =>
        current?.organizationUserUid === updated.organizationUserUid
          ? updated
          : current,
      );
      message.success(
        mutation === 'freeze'
          ? '用户已冻结，普通小程序会话已撤销'
          : mutation === 'restore'
            ? '用户已恢复，旧会话不会自动恢复'
            : mutation === 'grant-clean'
              ? '清运能力已授予，旧会话已撤销'
              : '清运能力已撤销，旧会话已撤销',
      );
      actionRef.current?.reload();
    } catch (error) {
      if (error instanceof ApiProblem && error.isVersionConflict) {
        await refreshUser(user);
        message.warning('数据版本已经变化，已载入最新状态；请核对后重新确认');
      }
    } finally {
      setMutating(null);
    }
  };

  const actionButtons = (user: OrganizationUser) => [
    <Button
      key="detail"
      type="link"
      size="small"
      icon={<EyeOutlined />}
      onClick={() => openDetail(user)}
    >
      详情
    </Button>,
    canFreeze ? (
      <Popconfirm
        key="status"
        title={
          user.status === 'ACTIVE'
            ? '冻结会撤销该用户的普通小程序会话，确认继续？'
            : '恢复账号不会恢复旧会话，确认继续？'
        }
        onConfirm={() =>
          mutate(user, user.status === 'ACTIVE' ? 'freeze' : 'restore')}
      >
        <Button
          type="link"
          size="small"
          danger={user.status === 'ACTIVE'}
          icon={user.status === 'ACTIVE' ? <StopOutlined /> : <UndoOutlined />}
          loading={
            mutating === (user.status === 'ACTIVE' ? 'freeze' : 'restore')
          }
        >
          {user.status === 'ACTIVE' ? '冻结' : '恢复'}
        </Button>
      </Popconfirm>
    ) : null,
    canManageCleaner ? (
      <Popconfirm
        key="clean"
        title={
          user.cleanOperationEnabled
            ? '撤销后清运入口将不再可用，确认继续？'
            : '确认授予该用户清运能力？'
        }
        onConfirm={() =>
          mutate(
            user,
            user.cleanOperationEnabled ? 'revoke-clean' : 'grant-clean',
          )}
      >
        <Button
          type="link"
          size="small"
          icon={
            user.cleanOperationEnabled
              ? <StopOutlined />
              : <ToolOutlined />
          }
          loading={
            mutating
            === (user.cleanOperationEnabled ? 'revoke-clean' : 'grant-clean')
          }
        >
          {user.cleanOperationEnabled ? '撤销清运' : '授予清运'}
        </Button>
      </Popconfirm>
    ) : null,
  ];

  const columns: ProColumns<OrganizationUser>[] = [
    {
      title: '机构用户',
      dataIndex: 'nickname',
      search: false,
      render: (_, user) => (
        <Space>
          <Avatar src={user.avatarUrl ?? undefined}>
            {userInitial(user)}
          </Avatar>
          <div>
            <div>{user.nickname}</div>
            <Typography.Text type="secondary" copyable>
              {user.organizationUserUid}
            </Typography.Text>
          </div>
        </Space>
      ),
    },
    {
      title: '手机号',
      dataIndex: 'phoneBound',
      valueType: 'select',
      valueEnum: {
        true: { text: '已绑定' },
        false: { text: '未绑定' },
      },
      render: (_, user) =>
        user.phoneBound ? user.maskedPhoneNumber ?? '已绑定' : '未绑定',
    },
    {
      title: '状态',
      dataIndex: 'status',
      valueType: 'select',
      valueEnum: {
        ACTIVE: { text: '正常' },
        FROZEN: { text: '已冻结' },
      },
      render: (_, user) => (
        <Tag color={user.status === 'ACTIVE' ? 'success' : 'default'}>
          {user.status === 'ACTIVE' ? '正常' : '已冻结'}
        </Tag>
      ),
    },
    {
      title: '清运能力',
      dataIndex: 'cleanOperation',
      valueType: 'select',
      valueEnum: {
        true: { text: '已授予' },
        false: { text: '未授予' },
      },
      render: (_, user) =>
        user.cleanOperationEnabled ? (
          <Tag icon={<CheckCircleOutlined />} color="success">已授予</Tag>
        ) : (
          <Tag>未授予</Tag>
        ),
    },
    {
      title: '来源部署',
      dataIndex: 'sourceDeploymentCode',
      render: (_, user) => user.registrationSource?.deploymentCode ?? '-',
    },
    {
      title: '注册时间',
      dataIndex: 'registeredAt',
      valueType: 'dateTime',
      search: false,
      width: 180,
    },
    {
      title: '注册起始',
      dataIndex: 'registeredFrom',
      valueType: 'dateTime',
      hideInTable: true,
    },
    {
      title: '注册截止',
      dataIndex: 'registeredTo',
      valueType: 'dateTime',
      hideInTable: true,
    },
    {
      title: '操作',
      valueType: 'option',
      width: 300,
      render: (_, user) => actionButtons(user),
    },
  ];

  return (
    <PageContainer
      {...pageHeader(
        '机构用户',
        '只展示安全投影；冻结、恢复和清运授权均使用服务端版本校验。',
      )}
    >
      <DirectoryScopeBar scope={scope} />
      {!scope.context && !scope.loading ? (
        <Empty description="请选择目标租户" />
      ) : !organizationOptions.length && !scope.loading ? (
        <Empty description="当前租户尚无机构" />
      ) : (
        <>
          <Space style={{ marginBottom: 16 }}>
            <Typography.Text strong>目标机构</Typography.Text>
            <Select
              aria-label="目标机构"
              showSearch
              optionFilterProp="label"
              style={{ width: 360 }}
              value={organizationCode}
              options={organizationOptions}
              onChange={setOrganizationCode}
            />
          </Space>
          <ProTable<OrganizationUser>
            {...proTableConfig}
            actionRef={actionRef}
            rowKey="organizationUserUid"
            columns={columns}
            scroll={{ x: 1180 }}
            request={async (params) => {
              if (!scope.context || !organizationCode) {
                return { data: [], total: 0, success: true };
              }
              try {
                const page = await listOrganizationUsers(
                  scope.context,
                  organizationCode,
                  {
                    page: params.current,
                    pageSize: params.pageSize,
                    status: params.status as OrganizationUser['status'] | undefined,
                    phoneBound:
                      params.phoneBound === undefined
                        ? undefined
                        : String(params.phoneBound) === 'true',
                    registeredFrom: params.registeredFrom
                      ? dayjs(params.registeredFrom as string).toISOString()
                      : undefined,
                    registeredTo: params.registeredTo
                      ? dayjs(params.registeredTo as string).toISOString()
                      : undefined,
                    sourceDeploymentCode:
                      params.sourceDeploymentCode as string | undefined,
                    cleanOperation:
                      params.cleanOperation === undefined
                        ? undefined
                        : String(params.cleanOperation) === 'true',
                  },
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
          />
        </>
      )}

      <Drawer
        title="机构用户详情"
        width={560}
        open={!!detail}
        loading={detailLoading}
        onClose={() => setDetail(null)}
        extra={detail ? <Space>{actionButtons(detail).slice(1)}</Space> : null}
      >
        {detail && (
          <ProDescriptions<OrganizationUser>
            column={1}
            dataSource={detail}
            bordered
          >
            <ProDescriptions.Item label="昵称">
              {detail.nickname}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="公开标识" copyable>
              {detail.organizationUserUid}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="手机号">
              {detail.phoneBound ? detail.maskedPhoneNumber ?? '已绑定' : '未绑定'}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="状态">
              {detail.status === 'ACTIVE' ? '正常' : '已冻结'}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="清运能力">
              {detail.cleanOperationEnabled ? '已授予' : '未授予'}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="注册时间">
              {dayjs(detail.registeredAt).format('YYYY-MM-DD HH:mm:ss')}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="注册来源">
              {detail.registrationSource
                ? `${detail.registrationSource.deploymentCode} · ${detail.registrationSource.lifecycleStatus}`
                : '无扫码来源'}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="版本">
              v{detail.version} / auth {detail.authVersion}
            </ProDescriptions.Item>
          </ProDescriptions>
        )}
      </Drawer>
    </PageContainer>
  );
}
