import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  LinkOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  type TableColumnsType,
} from 'antd';
import {
  getStaffAccount,
  getStaffMiniappBinding,
  listAllStaffAccounts,
  lookupOrganizationUserByPhone,
  revokeStaffMiniappBinding,
  setStaffMiniappBinding,
  type DirectoryContext,
  type OrganizationUserLookup,
  type StaffMiniappBindingLookup,
} from '@/api/identityDirectory';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import StaffCreateModal from '@/pages/staff/StaffCreateModal';
import { useAuthStore } from '@/stores/authStore';
import type { OrganizationUser, StaffAccount } from '@/types';

interface OrganizationUserStaffBindingPanelProps {
  context: DirectoryContext;
  organizationCode: string;
  user: OrganizationUser;
  canBind: boolean;
  onChanged?: () => void | Promise<void>;
}

type StaffStatusFilter = 'ALL' | 'ENABLED' | 'DISABLED';

function bindingTargetKey(
  context: DirectoryContext,
  organizationCode: string,
  organizationUserUid: string,
): string {
  return JSON.stringify([
    context.domain,
    context.tenantCode ?? null,
    organizationCode,
    organizationUserUid,
  ]);
}

export default function OrganizationUserStaffBindingPanel({
  context,
  organizationCode,
  user,
  canBind,
  onChanged,
}: OrganizationUserStaffBindingPanelProps) {
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const canCreateStaff = useAuthStore((state) =>
    state.hasCapability('staff.manage'));
  const requestSequence = useRef(0);
  const currentTarget = useRef('');
  const phoneNumber = user.phoneNumber ?? user.maskedPhoneNumber;
  const targetKey = bindingTargetKey(
    context,
    organizationCode,
    user.organizationUserUid,
  );
  currentTarget.current = targetKey;

  const [userLookup, setUserLookup] =
    useState<OrganizationUserLookup | null>(null);
  const [currentStaff, setCurrentStaff] = useState<StaffAccount | null>(null);
  const [staffAccounts, setStaffAccounts] = useState<StaffAccount[]>([]);
  const [selectedStaffUid, setSelectedStaffUid] = useState<string>();
  const [staffBinding, setStaffBinding] =
    useState<StaffMiniappBindingLookup | null>(null);
  const [staffBindingOwner, setStaffBindingOwner] = useState<string>();
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [chooserLoading, setChooserLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [issue, setIssue] = useState<string>();
  const [reason, setReason] = useState('');
  const [chooserOpen, setChooserOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] =
    useState<StaffStatusFilter>('ALL');

  const accepts = (requestId: number, requestedTarget: string) =>
    requestSequence.current === requestId
    && currentTarget.current === requestedTarget;

  const readUserLookup = useCallback(async () => {
    if (!canBind || !phoneNumber) return null;
    const next = await lookupOrganizationUserByPhone(
      context,
      organizationCode,
      phoneNumber,
      { silent: true },
    );
    if (next.organizationUserUid !== user.organizationUserUid) {
      throw new Error('手机号当前对应的机构用户已经变化，请关闭编辑窗口后重新打开核对');
    }
    return next;
  }, [canBind, context, organizationCode, phoneNumber, user.organizationUserUid]);

  const reloadSummary = useCallback(async () => {
    const requestId = ++requestSequence.current;
    const requestedTarget = targetKey;
    setSummaryLoading(true);
    setIssue(undefined);
    setUserLookup(null);
    setCurrentStaff(null);
    if (!canBind || !phoneNumber) {
      setSummaryLoading(false);
      return;
    }
    try {
      const nextLookup = await readUserLookup();
      if (!accepts(requestId, requestedTarget) || !nextLookup) return;
      setUserLookup(nextLookup);
      const currentUid = nextLookup.currentMiniappBinding?.staffAccountUid;
      if (!currentUid) return;
      const nextStaff = await getStaffAccount(context, currentUid);
      if (accepts(requestId, requestedTarget)) setCurrentStaff(nextStaff);
    } catch (error) {
      if (!accepts(requestId, requestedTarget)) return;
      setIssue(error instanceof Error
        ? error.message
        : '工作人员绑定信息加载失败，请稍后重试');
    } finally {
      if (accepts(requestId, requestedTarget)) setSummaryLoading(false);
    }
  }, [canBind, context, phoneNumber, readUserLookup, targetKey]);

  useEffect(() => {
    setChooserOpen(false);
    setCreateOpen(false);
    setReason('');
    void reloadSummary();
    return () => {
      requestSequence.current += 1;
    };
  }, [reloadSummary]);

  const loadChooser = useCallback(async (preferredStaffUid?: string) => {
    const requestId = ++requestSequence.current;
    const requestedTarget = targetKey;
    setChooserLoading(true);
    setIssue(undefined);
    setStaffBinding(null);
    setStaffBindingOwner(undefined);
    try {
      const [nextStaffAccounts, nextUserLookup] = await Promise.all([
        listAllStaffAccounts(context),
        readUserLookup(),
      ]);
      if (!accepts(requestId, requestedTarget) || !nextUserLookup) return;
      const candidates = nextStaffAccounts.filter(
        (staff) => staff.accountKind === 'STAFF',
      );
      const currentUid = nextUserLookup.currentMiniappBinding?.staffAccountUid;
      const nextSelectedUid = preferredStaffUid ?? currentUid;
      setStaffAccounts(candidates);
      setUserLookup(nextUserLookup);
      setCurrentStaff(
        candidates.find((staff) => staff.staffAccountUid === currentUid) ?? null,
      );
      setSelectedStaffUid(nextSelectedUid);
      if (!nextSelectedUid) return;
      const nextBinding = await getStaffMiniappBinding(
        context,
        organizationCode,
        nextSelectedUid,
      );
      if (!accepts(requestId, requestedTarget)) return;
      setStaffBinding(nextBinding);
      setStaffBindingOwner(nextSelectedUid);
    } catch (error) {
      if (!accepts(requestId, requestedTarget)) return;
      setIssue(error instanceof Error
        ? error.message
        : '工作人员目录加载失败，请稍后重试');
    } finally {
      if (accepts(requestId, requestedTarget)) setChooserLoading(false);
    }
  }, [context, organizationCode, readUserLookup, targetKey]);

  const openChooser = () => {
    setChooserOpen(true);
    setQuery('');
    setStatusFilter('ALL');
    setReason('');
    void loadChooser();
  };

  const selectStaff = async (staffUid: string) => {
    const requestId = ++requestSequence.current;
    const requestedTarget = targetKey;
    setSelectedStaffUid(staffUid);
    setStaffBinding(null);
    setStaffBindingOwner(undefined);
    setIssue(undefined);
    setChooserLoading(true);
    try {
      const nextBinding = await getStaffMiniappBinding(
        context,
        organizationCode,
        staffUid,
      );
      if (!accepts(requestId, requestedTarget)) return;
      setStaffBinding(nextBinding);
      setStaffBindingOwner(staffUid);
    } catch (error) {
      if (!accepts(requestId, requestedTarget)) return;
      setIssue(error instanceof Error
        ? error.message
        : '工作人员当前绑定读取失败，请稍后重试');
    } finally {
      if (accepts(requestId, requestedTarget)) setChooserLoading(false);
    }
  };

  const currentUserBinding = userLookup?.currentMiniappBinding;
  const currentStaffBinding = staffBinding?.currentMiniappBinding;
  const selectedStaff = staffAccounts.find(
    (staff) => staff.staffAccountUid === selectedStaffUid,
  );
  const sameBinding = !!selectedStaffUid
    && currentUserBinding?.staffAccountUid === selectedStaffUid
    && currentStaffBinding?.organizationUserUid === user.organizationUserUid;
  const willReplaceBinding = !!selectedStaffUid && !sameBinding && (
    !!currentUserBinding || !!currentStaffBinding
  );
  const snapshotsReady = !!userLookup
    && !!selectedStaffUid
    && staffBindingOwner === selectedStaffUid
    && !!staffBinding;

  const filteredStaff = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    return staffAccounts.filter((staff) => {
      if (statusFilter !== 'ALL' && staff.status !== statusFilter) return false;
      if (!keyword) return true;
      return [staff.displayName, staff.loginName, staff.contactPhone ?? '']
        .some((value) => value.toLocaleLowerCase().includes(keyword));
    });
  }, [query, staffAccounts, statusFilter]);
  const hasSelectableStaff = staffAccounts.some(
    (staff) => staff.status === 'ENABLED',
  );

  const bind = async () => {
    if (!selectedStaffUid || !userLookup || !staffBinding) return;
    const payload = {
      organizationUserUid: user.organizationUserUid,
      expectedStaffBinding: currentStaffBinding
        ? {
            bindingUid: currentStaffBinding.bindingUid,
            version: currentStaffBinding.version,
          }
        : null,
      expectedOrganizationUserBinding: currentUserBinding
        ? {
            bindingUid: currentUserBinding.bindingUid,
            version: currentUserBinding.version,
          }
        : null,
      reason: reason.trim() || undefined,
    };
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'set-staff-miniapp-binding',
          `${organizationCode}:${selectedStaffUid}`,
          payload,
        ),
        (intent) => setStaffMiniappBinding(
          context,
          organizationCode,
          selectedStaffUid,
          payload,
          intent,
        ),
      );
      message.success('工作人员已绑定，相关旧小程序会话已撤销');
      setChooserOpen(false);
      await onChanged?.();
      await reloadSummary();
    } catch (error) {
      if (error instanceof ApiProblem && error.isVersionConflict) {
        await loadChooser(selectedStaffUid);
        message.warning('绑定关系已经变化，已载入最新状态；请核对后重新确认');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const revoke = async () => {
    if (!currentUserBinding) return;
    const revokeReason = reason.trim() || undefined;
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'revoke-staff-miniapp-binding',
          currentUserBinding.bindingUid,
          {
            expectedVersion: currentUserBinding.version,
            reason: revokeReason,
          },
        ),
        (intent) => revokeStaffMiniappBinding(
          context,
          organizationCode,
          currentUserBinding,
          intent,
          revokeReason,
        ),
      );
      message.success('该机构用户的工作人员绑定已解除');
      setChooserOpen(false);
      await onChanged?.();
      await reloadSummary();
    } catch (error) {
      if (error instanceof ApiProblem && error.isVersionConflict) {
        await loadChooser();
        message.warning('绑定关系已经变化，已载入最新状态；请核对后重新确认');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const columns: TableColumnsType<StaffAccount> = [
    {
      title: '工作人员',
      key: 'staff',
      render: (_, staff) => (
        <div>
          <Typography.Text strong>{staff.displayName}</Typography.Text>
          <div><Typography.Text type="secondary">{staff.loginName}</Typography.Text></div>
        </div>
      ),
    },
    {
      title: '联系电话',
      dataIndex: 'contactPhone',
      render: (value) => value || '—',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (value) => (
        <Tag color={value === 'ENABLED' ? 'success' : 'default'}>
          {value === 'ENABLED' ? '已启用' : '已禁用'}
        </Tag>
      ),
    },
  ];

  if (!canBind) return null;

  return (
    <>
      <Card
        size="small"
        title={(
          <Space>
            <LinkOutlined />
            <span>工作人员绑定</span>
          </Space>
        )}
        extra={phoneNumber && (
          <Button type="primary" onClick={openChooser}>
            {currentUserBinding ? '选择或更换工作人员' : '选择工作人员'}
          </Button>
        )}
        style={{ marginTop: 16 }}
      >
        {!phoneNumber ? (
          <Alert
            showIcon
            type="warning"
            message="用户尚未绑定手机号，暂时不能绑定工作人员"
            description="请先让用户在当前机构小程序完成手机号授权，再回到这里操作。"
          />
        ) : (
          <Spin spinning={summaryLoading}>
            {issue && !chooserOpen && (
              <Alert showIcon type="error" message={issue} style={{ marginBottom: 12 }} />
            )}
            {currentUserBinding ? (
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="当前工作人员">
                  <Space wrap>
                    <Tag color="processing">已绑定</Tag>
                    <Typography.Text strong>
                      {currentStaff?.displayName ?? currentUserBinding.staffAccountUid}
                    </Typography.Text>
                    {currentStaff?.loginName && (
                      <Typography.Text type="secondary">
                        {currentStaff.loginName}
                      </Typography.Text>
                    )}
                  </Space>
                </Descriptions.Item>
                <Descriptions.Item label="绑定版本">
                  v{currentUserBinding.version}
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="该机构用户尚未绑定工作人员"
              />
            )}
          </Spin>
        )}
      </Card>

      <Modal
        title="选择工作人员"
        open={chooserOpen}
        width={820}
        destroyOnClose
        confirmLoading={submitting}
        okText={willReplaceBinding ? '确认换绑' : '确认绑定'}
        okButtonProps={{
          disabled: !snapshotsReady
            || selectedStaff?.status !== 'ENABLED'
            || sameBinding
            || !!issue,
        }}
        onOk={() => void bind()}
        onCancel={() => {
          if (!submitting) {
            requestSequence.current += 1;
            setChooserOpen(false);
            setIssue(undefined);
          }
        }}
        footer={(_, { OkBtn, CancelBtn }) => (
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <div>
              {currentUserBinding && (
                <Popconfirm
                  title="解除该机构用户的工作人员绑定？"
                  description="当前工作人员的管理小程序会话将立即失效。"
                  onConfirm={() => void revoke()}
                >
                  <Button danger loading={submitting}>解除当前绑定</Button>
                </Popconfirm>
              )}
            </div>
            <Space><CancelBtn /><OkBtn /></Space>
          </Space>
        )}
      >
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              aria-label="搜索工作人员"
              placeholder="搜索姓名、登录名或联系电话"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              style={{ width: 360 }}
            />
            <Select<StaffStatusFilter>
              aria-label="工作人员状态"
              value={statusFilter}
              onChange={setStatusFilter}
              style={{ width: 150 }}
              options={[
                { value: 'ALL', label: '全部状态' },
                { value: 'ENABLED', label: '已启用' },
                { value: 'DISABLED', label: '已禁用' },
              ]}
            />
          </Space>
          {issue && <Alert showIcon type="error" message={issue} />}
          {!chooserLoading && !hasSelectableStaff && (
            <Alert
              showIcon
              type="info"
              message="当前租户没有可选择的已启用工作人员"
              description="已禁用账号仍会展示，但不能用于新的小程序绑定。"
              action={canCreateStaff ? (
                <Button
                  type="primary"
                  icon={<PlusOutlined />}
                  onClick={() => setCreateOpen(true)}
                >
                  新建工作人员
                </Button>
              ) : (
                <Typography.Text type="secondary">
                  请联系管理员创建或启用账号
                </Typography.Text>
              )}
            />
          )}
          <Table<StaffAccount>
            rowKey="staffAccountUid"
            size="small"
            loading={chooserLoading}
            columns={columns}
            dataSource={filteredStaff}
            pagination={{ pageSize: 8, hideOnSinglePage: true }}
            rowSelection={{
              type: 'radio',
              selectedRowKeys: selectedStaffUid ? [selectedStaffUid] : [],
              getCheckboxProps: (staff) => ({
                disabled: staff.status !== 'ENABLED',
                name: staff.displayName,
                'aria-label': staff.displayName,
              }),
              onChange: (keys) => {
                const next = String(keys[0] ?? '');
                if (next) void selectStaff(next);
              },
            }}
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="没有匹配筛选条件的工作人员"
                >
                  <Button onClick={() => {
                    setQuery('');
                    setStatusFilter('ALL');
                  }}>
                    清除筛选
                  </Button>
                </Empty>
              ),
            }}
          />

          {selectedStaffUid && staffBindingOwner === selectedStaffUid && (
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="所选工作人员">
                {selectedStaff?.displayName ?? selectedStaffUid}
              </Descriptions.Item>
              <Descriptions.Item label="该工作人员当前绑定">
                {currentStaffBinding
                  ? `${currentStaffBinding.nickname ?? currentStaffBinding.organizationUserUid} · v${currentStaffBinding.version}`
                  : '无'}
              </Descriptions.Item>
            </Descriptions>
          )}
          {willReplaceBinding && (
            <Alert
              showIcon
              type="warning"
              message="本次操作会替换现有绑定"
              description="提交时会再次核对用户侧和工作人员侧版本，不会静默覆盖并发变更。"
            />
          )}
          {sameBinding && (
            <Alert
              showIcon
              type="success"
              icon={<SafetyCertificateOutlined />}
              message="该用户已经绑定到所选工作人员"
            />
          )}
          <Input.TextArea
            aria-label="工作人员绑定原因"
            value={reason}
            maxLength={500}
            showCount
            autoSize={{ minRows: 2, maxRows: 4 }}
            placeholder="原因（可选，会进入安全审计）"
            onChange={(event) => setReason(event.target.value)}
          />
        </Space>
      </Modal>

      <StaffCreateModal
        context={context}
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={async (staff) => {
          setChooserOpen(true);
          await loadChooser(staff.staffAccountUid);
        }}
      />
    </>
  );
}
