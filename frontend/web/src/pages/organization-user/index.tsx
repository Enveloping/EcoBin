import { useEffect, useRef, useState } from 'react';
import {
  EyeOutlined,
  StopOutlined,
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
import { Link, useSearchParams } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  changeOrganizationUserStatus,
  getOrganizationUser,
  listOrganizationUsers,
} from '@/api/identityDirectory';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { useAuthStore } from '@/stores/authStore';
import type { OrganizationUser } from '@/types';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import { directoryPath } from '@/router/directoryQuery';

type UserMutation = 'freeze' | 'restore';

function userInitial(user: OrganizationUser): string {
  return user.nickname.trim().slice(0, 1).toUpperCase() || '用';
}

export default function OrganizationUserPage() {
  const scope = useDirectoryScope();
  const organizationScope = useOrganizationScope(scope);
  const [searchParams] = useSearchParams();
  const view = searchParams.get('view') === 'disabled' ? 'disabled' : 'all';
  const { message } = App.useApp();
  const actionRef = useRef<ActionType>(null);
  const executeCommand = useCommandExecutor();
  const canFreeze = useAuthStore((state) =>
    state.hasCapability('user.freeze'));
  const canReadDelivery = useAuthStore((state) =>
    state.hasCapability('delivery.read')
    || state.hasCapability('review.execute'));
  const canReadWithdrawal = useAuthStore((state) =>
    state.hasCapability('withdrawal.read')
    || state.hasCapability('review.execute'));
  const organizationCode = organizationScope.organizationCode;
  const [detail, setDetail] = useState<OrganizationUser | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [mutating, setMutating] = useState<UserMutation | null>(null);

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
    const enabled = mutation === 'restore';
    const reason = {
      freeze: 'Web 管理端冻结机构用户',
      restore: 'Web 管理端恢复机构用户',
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
          changeOrganizationUserStatus(
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
          : '用户已恢复，旧会话不会自动恢复',
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
      title: '注册设备',
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
      title: '关联记录',
      key: 'relatedRecords',
      search: false,
      width: 150,
      render: (_, user) => (
        <Space size={12}>
          {canReadDelivery && (
            <Link
              to={directoryPath('/deliveries', {
                tenant: scope.platform ? scope.tenantCode : undefined,
                organization: organizationCode,
                organizationUserUid: user.organizationUserUid,
              })}
            >
              投递
            </Link>
          )}
          {canReadWithdrawal && (
            <Link
              to={directoryPath('/withdrawals', {
                tenant: scope.platform ? scope.tenantCode : undefined,
                organization: organizationCode,
                organizationUserUid: user.organizationUserUid,
              })}
            >
              提现
            </Link>
          )}
          {!canReadDelivery && !canReadWithdrawal && (
            <Typography.Text type="secondary">无读取权限</Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'operation',
      valueType: 'option',
      width: 150,
      hideInSetting: true,
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
      ) : !organizationScope.organizationOptions.length
        && !scope.loading
        && !organizationScope.loading ? (
        <Empty description="当前租户尚无机构" />
      ) : (
          <ProTable<OrganizationUser>
            {...proTableConfig}
            actionRef={actionRef}
            rowKey="organizationUserUid"
            columns={columns}
            params={{ view }}
            columnsState={{
              persistenceKey: 'ecobin.web.columns.organization-users.v1',
              persistenceType: 'localStorage',
            }}
            headerTitle={(
              <Space>
                <Typography.Text strong>目标机构</Typography.Text>
                <Select
                  aria-label="目标机构"
                  showSearch
                  optionFilterProp="label"
                  style={{ width: 360 }}
                  value={organizationCode}
                  options={organizationScope.organizationOptions}
                  loading={organizationScope.loading}
                  onChange={organizationScope.setOrganizationCode}
                />
              </Space>
            )}
            scroll={{ x: 1100 }}
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
                    status: view === 'disabled'
                      ? 'FROZEN'
                      : params.status as
                        | OrganizationUser['status']
                        | undefined,
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
