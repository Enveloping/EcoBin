import { useEffect, useRef, useState } from 'react';
import {
  DollarOutlined,
  EditOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import {
  PageContainer,
  ProDescriptions,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  App,
  Avatar,
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  adjustOrganizationUserWallet,
  getOrganizationUserWallet,
  type WalletSummary,
} from '@/api/funds';
import {
  changeOrganizationUserCleanOperation,
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
import {
  LatestTargetRequestGuard,
  walletPreviewTargetKey,
} from '@/utils/latestTargetRequest';
import OrganizationUserWalletDrawer from
  '@/pages/wallet-entries/OrganizationUserWalletDrawer';
import {
  isMoneyInputDraft,
  normalizeMoneyInput,
  parseMoneyInputCent,
} from '@/utils/decimal';
import OrganizationUserStaffBindingPanel from
  './OrganizationUserStaffBindingPanel';

type UserMutation = 'freeze' | 'restore';
type CleanMutation = 'grant-clean' | 'revoke-clean';

function userInitial(user: OrganizationUser): string {
  return user.nickname.trim().slice(0, 1).toUpperCase() || '用';
}

function formatMoneyCent(value: bigint): string {
  const negative = value < 0n;
  const absolute = negative ? -value : value;
  return `${negative ? '-' : ''}${absolute / 100n}.${String(
    absolute % 100n,
  ).padStart(2, '0')}`;
}

export default function OrganizationUserPage() {
  const scope = useDirectoryScope();
  const organizationScope = useOrganizationScope(scope);
  const [searchParams] = useSearchParams();
  const view = searchParams.get('view') === 'disabled' ? 'disabled' : 'all';
  const { message, modal } = App.useApp();
  const actionRef = useRef<ActionType>(null);
  const detailRequestSequence = useRef(0);
  const walletPreviewRequests = useRef(new LatestTargetRequestGuard());
  const selectedWalletAdjustmentTarget = useRef<string | null>(null);
  const walletPreviewOwner = useRef<string | null>(null);
  const executeCommand = useCommandExecutor();
  const canFreeze = useAuthStore((state) =>
    state.hasCapability('user.freeze'));
  const canManageCleaner = useAuthStore((state) =>
    state.hasCapability('cleaner.manage'));
  const canBindStaff = useAuthStore((state) =>
    state.hasCapability('staff.bind'));
  const canReadStaff = useAuthStore((state) =>
    state.hasCapability('staff.read'));
  const canAdjustWallet = useAuthStore((state) =>
    state.hasCapability('wallet.adjust')) || scope.platform;
  const canReadDelivery = useAuthStore((state) =>
    state.hasCapability('delivery.read')
    || state.hasCapability('review.execute'));
  const canReadWalletDelivery = useAuthStore((state) =>
    state.hasCapability('delivery.read'));
  const canReadWithdrawal = useAuthStore((state) =>
    state.hasCapability('withdrawal.read')
    || state.hasCapability('review.execute'));
  const canReadWallet = useAuthStore((state) =>
    state.hasCapability('wallet.read'));
  const canReadClean = useAuthStore((state) =>
    state.hasCapability('clean.read'));
  const organizationCode = organizationScope.organizationCode;
  const [detail, setDetail] = useState<OrganizationUser | null>(null);
  const [walletUser, setWalletUser] = useState<OrganizationUser | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [mutating, setMutating] = useState<UserMutation | null>(null);
  const [cleanMutating, setCleanMutating] =
    useState<CleanMutation | null>(null);
  const [adjustingUser, setAdjustingUser] =
    useState<OrganizationUser | null>(null);
  const [walletPreview, setWalletPreview] = useState<WalletSummary | null>(null);
  const [adjustDeltaYuan, setAdjustDeltaYuan] = useState('');
  const [adjustReason, setAdjustReason] = useState('');
  const [adjustLoading, setAdjustLoading] = useState(false);
  const [adjustSubmitting, setAdjustSubmitting] = useState(false);
  const walletAdjustmentScopeKey = scope.context && organizationCode
    ? JSON.stringify([
      scope.context.domain,
      scope.context.tenantCode ?? null,
      organizationCode,
    ])
    : '';
  const currentWalletAdjustmentScope = useRef(walletAdjustmentScopeKey);
  currentWalletAdjustmentScope.current = walletAdjustmentScopeKey;

  useEffect(() => {
    detailRequestSequence.current += 1;
    actionRef.current?.reload();
    setDetail(null);
    setWalletUser(null);
    walletPreviewRequests.current.invalidate();
    selectedWalletAdjustmentTarget.current = null;
    walletPreviewOwner.current = null;
    setAdjustingUser(null);
    setWalletPreview(null);
    setAdjustLoading(false);
  }, [walletAdjustmentScopeKey]);

  useEffect(() => () => {
    walletPreviewRequests.current.invalidate();
  }, []);

  const openDetail = async (user: OrganizationUser) => {
    if (!scope.context || !organizationCode) return;
    const requestId = ++detailRequestSequence.current;
    setDetail(user);
    setDetailLoading(true);
    try {
      const latest = await getOrganizationUser(
        scope.context,
        organizationCode,
        user.organizationUserUid,
      );
      if (detailRequestSequence.current === requestId) setDetail(latest);
    } finally {
      if (detailRequestSequence.current === requestId) {
        setDetailLoading(false);
      }
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
      freeze: 'Web 管理端禁用机构用户',
      restore: 'Web 管理端启用机构用户',
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
          ? '用户已禁用，普通小程序会话已撤销'
          : '用户已启用，旧会话不会自动恢复',
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

  const mutateCleanOperation = async (user: OrganizationUser) => {
    if (!scope.context || !organizationCode) return;
    const enabled = !user.cleanOperationEnabled;
    const mutation: CleanMutation = enabled ? 'grant-clean' : 'revoke-clean';
    const reason = enabled
      ? 'Web 管理端授予机构用户清运操作资格'
      : 'Web 管理端撤销机构用户清运操作资格';
    setCleanMutating(mutation);
    try {
      const updated = await executeCommand(
        commandKey(mutation, user.organizationUserUid, {
          expectedVersion: user.version,
          expectedAuthVersion: user.authVersion,
          reason,
        }),
        (intent) => changeOrganizationUserCleanOperation(
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
        enabled
          ? '已设为清运员，该用户可在小程序发起清运操作'
          : '清运员资格已撤销，后续清运操作将被阻止',
      );
      actionRef.current?.reload();
    } catch (error) {
      if (error instanceof ApiProblem && error.isVersionConflict) {
        await refreshUser(user);
        message.warning('用户权限版本已经变化，已载入最新状态；请重新核对');
      }
    } finally {
      setCleanMutating(null);
    }
  };

  const loadWalletPreview = async (user: OrganizationUser) => {
    const context = scope.context;
    const requestedOrganization = organizationCode;
    const requestedScope = walletAdjustmentScopeKey;
    if (!context || !requestedOrganization || !requestedScope) return null;
    const targetKey = walletPreviewTargetKey(
      context.domain,
      context.tenantCode,
      requestedOrganization,
      user.organizationUserUid,
    );
    if (
      currentWalletAdjustmentScope.current !== requestedScope
      || selectedWalletAdjustmentTarget.current !== targetKey
    ) return null;

    const ticket = walletPreviewRequests.current.begin(targetKey);
    walletPreviewOwner.current = null;
    setWalletPreview(null);
    setAdjustLoading(true);
    try {
      const preview = await getOrganizationUserWallet(
        context,
        requestedOrganization,
        user.organizationUserUid,
        ticket.signal,
      );
      if (
        !walletPreviewRequests.current.accepts(ticket, targetKey)
        || currentWalletAdjustmentScope.current !== requestedScope
        || selectedWalletAdjustmentTarget.current !== targetKey
      ) return null;
      walletPreviewOwner.current = targetKey;
      setWalletPreview(preview);
      return preview;
    } catch (error) {
      if (
        !walletPreviewRequests.current.accepts(ticket, targetKey)
        || currentWalletAdjustmentScope.current !== requestedScope
        || selectedWalletAdjustmentTarget.current !== targetKey
      ) return null;
      throw error;
    } finally {
      if (
        walletPreviewRequests.current.accepts(ticket, targetKey)
        && currentWalletAdjustmentScope.current === requestedScope
        && selectedWalletAdjustmentTarget.current === targetKey
      ) setAdjustLoading(false);
    }
  };

  const openWalletAdjustment = async (user: OrganizationUser) => {
    if (!scope.context || !organizationCode) return;
    const targetKey = walletPreviewTargetKey(
      scope.context.domain,
      scope.context.tenantCode,
      organizationCode,
      user.organizationUserUid,
    );
    walletPreviewRequests.current.invalidate();
    selectedWalletAdjustmentTarget.current = targetKey;
    walletPreviewOwner.current = null;
    setAdjustingUser(user);
    setWalletPreview(null);
    setAdjustDeltaYuan('');
    setAdjustReason('');
    try {
      await loadWalletPreview(user);
    } catch {
      if (selectedWalletAdjustmentTarget.current === targetKey) {
        walletPreviewRequests.current.invalidate();
        selectedWalletAdjustmentTarget.current = null;
        walletPreviewOwner.current = null;
        setAdjustingUser(null);
        setWalletPreview(null);
      }
      setAdjustLoading(false);
    }
  };

  const submitWalletAdjustment = async () => {
    const context = scope.context;
    const requestedOrganization = organizationCode;
    const user = adjustingUser;
    if (!context || !requestedOrganization || !user || !walletPreview) return;
    const targetKey = walletPreviewTargetKey(
      context.domain,
      context.tenantCode,
      requestedOrganization,
      user.organizationUserUid,
    );
    if (
      selectedWalletAdjustmentTarget.current !== targetKey
      || walletPreviewOwner.current !== targetKey
    ) {
      message.warning('余额预览已经失效，请重新打开后核对');
      return;
    }
    const normalizedDelta = normalizeMoneyInput(adjustDeltaYuan, true);
    const deltaCent = parseMoneyInputCent(adjustDeltaYuan, true);
    if (!normalizedDelta || deltaCent === null || deltaCent === 0n) {
      message.warning('请输入最多两位小数且不为 0 的调整差额');
      return;
    }
    const data = {
      deltaYuan: normalizedDelta,
      expectedWalletVersion: walletPreview.walletVersion,
      reason: adjustReason.trim() || null,
    };
    setAdjustSubmitting(true);
    try {
      const result = await executeCommand(
        commandKey('wallet-adjust', user.organizationUserUid, data),
        (intent) => adjustOrganizationUserWallet(
          context,
          requestedOrganization,
          user.organizationUserUid,
          data,
          intent,
        ),
      );
      message.success(
        `余额已由 ¥${result.availableBalanceBeforeYuan} 调整为 ¥${result.availableBalanceAfterYuan}`,
      );
      walletPreviewRequests.current.invalidate();
      selectedWalletAdjustmentTarget.current = null;
      walletPreviewOwner.current = null;
      setAdjustingUser(null);
      setWalletPreview(null);
    } catch (error) {
      if (error instanceof ApiProblem && error.isVersionConflict) {
        const latest = await loadWalletPreview(user);
        if (latest) {
          message.warning('钱包余额已经变化，已载入最新余额；请重新核对');
        }
      }
    } finally {
      setAdjustSubmitting(false);
    }
  };

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
        ACTIVE: { text: '已启用' },
        FROZEN: { text: '已禁用' },
      },
      render: (_, user) => (
        <Tag color={user.status === 'ACTIVE' ? 'success' : 'default'}>
          {user.status === 'ACTIVE' ? '已启用' : '已禁用'}
        </Tag>
      ),
    },
    {
      title: '清运权限',
      dataIndex: 'cleanOperation',
      valueType: 'select',
      valueEnum: {
        true: { text: '清运员' },
        false: { text: '普通用户' },
      },
      render: (_, user) => (
        <Tag color={user.cleanOperationEnabled ? 'blue' : 'default'}>
          {user.cleanOperationEnabled ? '清运员' : '普通用户'}
        </Tag>
      ),
    },
    {
      title: '注册设备',
      dataIndex: 'sourceDeviceCode',
      render: (_, user) => user.registrationSource?.deviceCode ?? '-',
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
      width: 230,
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
          {canReadClean && (
            <Link
              to={directoryPath('/clean-records', {
                tenant: scope.platform ? scope.tenantCode : undefined,
                organization: organizationCode,
                cleanerUserUid: user.organizationUserUid,
              })}
            >
              清运
            </Link>
          )}
          {canReadWallet && (
            <Button
              type="link"
              size="small"
              icon={<WalletOutlined />}
              style={{ paddingInline: 0 }}
              onClick={() => setWalletUser(user)}
            >
              钱包
            </Button>
          )}
          {!canReadDelivery && !canReadWithdrawal && !canReadWallet
            && !canReadClean && (
            <Typography.Text type="secondary">无读取权限</Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'operation',
      valueType: 'option',
      width: 90,
      hideInSetting: true,
      render: (_, user) => [
        <Button
          key="edit"
          type="link"
          size="small"
          icon={<EditOutlined />}
          onClick={() => openDetail(user)}
        >
          编辑
        </Button>,
      ],
    },
  ];

  const adjustmentCent = parseMoneyInputCent(adjustDeltaYuan, true);

  return (
    <PageContainer
      {...pageHeader(
        '机构用户',
        '用户启用、禁用和清运资格调整均使用服务端版本校验。',
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
                    cleanOperation:
                      params.cleanOperation === undefined
                        ? undefined
                        : String(params.cleanOperation) === 'true',
                    registeredFrom: params.registeredFrom
                      ? dayjs(params.registeredFrom as string).toISOString()
                      : undefined,
                    registeredTo: params.registeredTo
                      ? dayjs(params.registeredTo as string).toISOString()
                      : undefined,
                    sourceDeviceCode:
                      params.sourceDeviceCode as string | undefined,
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
        title="编辑机构用户"
        width={760}
        open={!!detail}
        loading={detailLoading}
        onClose={() => {
          detailRequestSequence.current += 1;
          setDetail(null);
          setDetailLoading(false);
        }}
      >
        {detail && (
          <>
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
                {detail.phoneBound
                  ? detail.maskedPhoneNumber ?? '已绑定'
                  : '未绑定'}
              </ProDescriptions.Item>
              <ProDescriptions.Item label="状态">
                {detail.status === 'ACTIVE' ? '已启用' : '已禁用'}
              </ProDescriptions.Item>
              <ProDescriptions.Item label="清运操作资格">
                <Tag color={detail.cleanOperationEnabled ? 'blue' : 'default'}>
                  {detail.cleanOperationEnabled ? '清运员' : '普通用户'}
                </Tag>
              </ProDescriptions.Item>
              <ProDescriptions.Item label="注册时间">
                {dayjs(detail.registeredAt).format('YYYY-MM-DD HH:mm:ss')}
              </ProDescriptions.Item>
              <ProDescriptions.Item label="注册来源">
                {detail.registrationSource
                  ? `${detail.registrationSource.deviceCode} · ${detail.registrationSource.lifecycleStatus}`
                  : '无扫码来源'}
              </ProDescriptions.Item>
              <ProDescriptions.Item label="版本">
                v{detail.version} / auth {detail.authVersion}
              </ProDescriptions.Item>
            </ProDescriptions>
            <Card
              size="small"
              title="用户设置"
              style={{ marginTop: 16 }}
            >
              <Space direction="vertical" size={18} style={{ width: '100%' }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 16,
                  }}
                >
                  <div>
                    <Typography.Text strong>账号状态</Typography.Text>
                    <div>
                      <Typography.Text type="secondary">
                        禁用会撤销该用户的普通小程序会话。
                      </Typography.Text>
                    </div>
                  </div>
                  <Radio.Group
                    aria-label="机构用户账号状态"
                    optionType="button"
                    buttonStyle="solid"
                    value={detail.status}
                    disabled={!canFreeze || !!mutating || !!cleanMutating}
                    options={[
                      { value: 'ACTIVE', label: '启用' },
                      { value: 'FROZEN', label: '禁用' },
                    ]}
                    onChange={(event) => {
                      const enable = event.target.value === 'ACTIVE';
                      if (enable === (detail.status === 'ACTIVE')) return;
                      modal.confirm({
                        title: enable ? '确认启用该用户？' : '确认禁用该用户？',
                        content: enable
                          ? '启用后旧会话不会自动恢复，用户需要重新登录。'
                          : '禁用后普通小程序会话立即撤销，后续用户业务将被阻止。',
                        okText: enable ? '确认启用' : '确认禁用',
                        okButtonProps: { danger: !enable },
                        onOk: () => mutate(
                          detail,
                          enable ? 'restore' : 'freeze',
                        ),
                      });
                    }}
                  />
                </div>

                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 16,
                  }}
                >
                  <div>
                    <Typography.Text strong>清运资格</Typography.Text>
                    <div>
                      <Typography.Text type="secondary">
                        清运员可以在工作人员小程序发起清运操作。
                      </Typography.Text>
                    </div>
                  </div>
                  <Radio.Group
                    aria-label="机构用户清运资格"
                    optionType="button"
                    buttonStyle="solid"
                    value={detail.cleanOperationEnabled ? 'CLEANER' : 'USER'}
                    disabled={!canManageCleaner || !!mutating || !!cleanMutating}
                    options={[
                      { value: 'USER', label: '普通用户' },
                      { value: 'CLEANER', label: '清运员' },
                    ]}
                    onChange={(event) => {
                      const enable = event.target.value === 'CLEANER';
                      if (enable === detail.cleanOperationEnabled) return;
                      modal.confirm({
                        title: enable ? '确认设为清运员？' : '确认取消清运员资格？',
                        content: enable
                          ? '确认后该用户可以发起新的清运操作。'
                          : '取消后该用户不能再发起新的清运操作。',
                        okText: '确认',
                        okButtonProps: { danger: !enable },
                        onOk: () => mutateCleanOperation(detail),
                      });
                    }}
                  />
                </div>

                {(canReadWallet || canAdjustWallet) && (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 16,
                    }}
                  >
                    <div>
                      <Typography.Text strong>用户钱包</Typography.Text>
                      <div>
                        <Typography.Text type="secondary">
                          查看资金明细或创建一笔人工余额调整。
                        </Typography.Text>
                      </div>
                    </div>
                    <Space wrap>
                      {canReadWallet && (
                        <Button
                          icon={<WalletOutlined />}
                          onClick={() => setWalletUser(detail)}
                        >
                          查看钱包
                        </Button>
                      )}
                      {canAdjustWallet && (
                        <Button
                          type="primary"
                          icon={<DollarOutlined />}
                          onClick={() => void openWalletAdjustment(detail)}
                        >
                          调整余额
                        </Button>
                      )}
                    </Space>
                  </div>
                )}
              </Space>
            </Card>
            {canBindStaff && !canReadStaff && (
              <Alert
                showIcon
                type="info"
                message="绑定工作人员还需要工作人员读取权限"
                description="当前账号已有 staff.bind（绑定工作人员）权限，但没有 staff.read（读取工作人员）权限，因此无法安全列出可绑定账号。"
                style={{ marginTop: 16 }}
              />
            )}
            {scope.context
              && organizationCode
              && canBindStaff
              && canReadStaff && (
                <OrganizationUserStaffBindingPanel
                  context={scope.context}
                  organizationCode={organizationCode}
                  user={detail}
                  canBind
                  onChanged={() => refreshUser(detail)}
                />
              )}
          </>
        )}
      </Drawer>

      <Modal
        title="人工调整用户余额"
        open={!!adjustingUser}
        confirmLoading={adjustSubmitting}
        okText="确认记账"
        cancelText="取消"
        okButtonProps={{
          disabled: adjustLoading
            || !walletPreview
            || walletPreviewOwner.current
              !== selectedWalletAdjustmentTarget.current
            || adjustmentCent === null
            || adjustmentCent === 0n,
        }}
        onOk={submitWalletAdjustment}
        onCancel={() => {
          if (!adjustSubmitting) {
            walletPreviewRequests.current.invalidate();
            selectedWalletAdjustmentTarget.current = null;
            walletPreviewOwner.current = null;
            setAdjustingUser(null);
            setWalletPreview(null);
            setAdjustLoading(false);
          }
        }}
      >
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Typography.Text>
            目标用户：{adjustingUser?.nickname ?? '-'}
          </Typography.Text>
          <Typography.Text>
            当前可用余额：
            {adjustLoading
              ? '读取中…'
              : `¥${walletPreview?.availableBalanceYuan ?? '-'}`}
          </Typography.Text>
          <Input
            aria-label="余额调整差额"
            addonBefore="差额 ¥"
            placeholder="例如 10、0.4 或 -10.5"
            inputMode="decimal"
            value={adjustDeltaYuan}
            onChange={(event) => {
              const next = event.target.value;
              if (isMoneyInputDraft(next, true)) setAdjustDeltaYuan(next);
            }}
            onBlur={() => {
              const normalized = normalizeMoneyInput(adjustDeltaYuan, true);
              if (normalized) setAdjustDeltaYuan(normalized);
            }}
          />
          <Typography.Text type="secondary">
            正数增加余额，负数扣减余额；金额最多保留两位小数。
          </Typography.Text>
          <Typography.Text type="secondary">
            调整后预计余额：
            {(() => {
              const before = walletPreview
                ? parseMoneyInputCent(
                  walletPreview.availableBalanceYuan,
                  true,
                )
                : null;
              const delta = parseMoneyInputCent(adjustDeltaYuan, true);
              return before === null || delta === null
                ? '-'
                : `¥${formatMoneyCent(before + delta)}`;
            })()}
          </Typography.Text>
          <Input.TextArea
            aria-label="余额调整原因"
            maxLength={500}
            showCount
            rows={3}
            placeholder="原因可选；建议填写便于后续审计核对"
            value={adjustReason}
            onChange={(event) => setAdjustReason(event.target.value)}
          />
          <Typography.Text type="warning">
            提交后会生成不可修改的钱包明细；只调整可用余额，不修改提现冻结金额。
          </Typography.Text>
        </Space>
      </Modal>

      <OrganizationUserWalletDrawer
        open={!!walletUser}
        context={scope.context}
        organizationCode={organizationCode}
        user={walletUser}
        canReadDelivery={canReadWalletDelivery}
        onClose={() => setWalletUser(null)}
      />
    </PageContainer>
  );
}
