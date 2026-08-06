import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  LinkOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import {
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
import type { OrganizationUser, StaffAccount } from '@/types';

interface OrganizationUserStaffBindingPanelProps {
  context: DirectoryContext;
  organizationCode: string;
  user: OrganizationUser;
  canBind: boolean;
  onChanged?: () => void | Promise<void>;
}

interface StaffOption {
  value: string;
  label: string;
  disabled: boolean;
}

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
  const requestSequence = useRef(0);
  const currentTarget = useRef('');
  const phoneNumber = user.phoneNumber ?? user.maskedPhoneNumber;
  const targetKey = bindingTargetKey(
    context,
    organizationCode,
    user.organizationUserUid,
  );
  currentTarget.current = targetKey;

  const [staffAccounts, setStaffAccounts] = useState<StaffAccount[]>([]);
  const [userLookup, setUserLookup] =
    useState<OrganizationUserLookup | null>(null);
  const [selectedStaffUid, setSelectedStaffUid] = useState<string>();
  const [staffBinding, setStaffBinding] =
    useState<StaffMiniappBindingLookup | null>(null);
  const [staffBindingOwner, setStaffBindingOwner] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [issue, setIssue] = useState<string>();
  const [reason, setReason] = useState('');

  const accepts = (requestId: number, requestedTarget: string) =>
    requestSequence.current === requestId
    && currentTarget.current === requestedTarget;

  const reload = useCallback(async (preferredStaffUid?: string) => {
    const requestId = ++requestSequence.current;
    const requestedTarget = targetKey;
    setLoading(true);
    setIssue(undefined);
    setStaffBinding(null);
    setStaffBindingOwner(undefined);
    if (!canBind || !phoneNumber) {
      setStaffAccounts([]);
      setUserLookup(null);
      setSelectedStaffUid(undefined);
      setLoading(false);
      return;
    }

    try {
      const [nextStaffAccounts, nextUserLookup] = await Promise.all([
        listAllStaffAccounts(context),
        lookupOrganizationUserByPhone(
          context,
          organizationCode,
          phoneNumber,
          { silent: true },
        ),
      ]);
      if (!accepts(requestId, requestedTarget)) return;
      if (nextUserLookup.organizationUserUid !== user.organizationUserUid) {
        setIssue('手机号当前对应的机构用户已经变化，已停止绑定；请关闭详情后重新打开核对。');
        setStaffAccounts(nextStaffAccounts);
        setUserLookup(null);
        setSelectedStaffUid(undefined);
        return;
      }

      const currentStaffUid =
        nextUserLookup.currentMiniappBinding?.staffAccountUid;
      const nextSelectedStaffUid = preferredStaffUid ?? currentStaffUid;
      setStaffAccounts(nextStaffAccounts);
      setUserLookup(nextUserLookup);
      setSelectedStaffUid(nextSelectedStaffUid);
      if (!nextSelectedStaffUid) return;

      const nextStaffBinding = await getStaffMiniappBinding(
        context,
        organizationCode,
        nextSelectedStaffUid,
      );
      if (!accepts(requestId, requestedTarget)) return;
      setStaffBinding(nextStaffBinding);
      setStaffBindingOwner(nextSelectedStaffUid);
    } catch (error) {
      if (!accepts(requestId, requestedTarget)) return;
      setIssue(
        error instanceof Error
          ? error.message
          : '工作人员绑定信息加载失败，请稍后重试',
      );
    } finally {
      if (accepts(requestId, requestedTarget)) setLoading(false);
    }
  }, [
    canBind,
    context,
    organizationCode,
    phoneNumber,
    targetKey,
    user.organizationUserUid,
  ]);

  useEffect(() => {
    setReason('');
    void reload();
    return () => {
      requestSequence.current += 1;
    };
  }, [reload]);

  const staffOptions = useMemo<StaffOption[]>(
    () => staffAccounts
      .filter((staff) => staff.accountKind === 'STAFF')
      .map((staff) => ({
        value: staff.staffAccountUid,
        label: `${staff.displayName} · ${staff.loginName}${
          staff.status === 'ENABLED' ? '' : '（已停用）'
        }`,
        disabled: staff.status !== 'ENABLED',
      })),
    [staffAccounts],
  );

  const selectStaff = async (staffUid: string) => {
    const requestId = ++requestSequence.current;
    const requestedTarget = targetKey;
    setSelectedStaffUid(staffUid);
    setStaffBinding(null);
    setStaffBindingOwner(undefined);
    setIssue(undefined);
    setLoading(true);
    try {
      const nextStaffBinding = await getStaffMiniappBinding(
        context,
        organizationCode,
        staffUid,
      );
      if (!accepts(requestId, requestedTarget)) return;
      setStaffBinding(nextStaffBinding);
      setStaffBindingOwner(staffUid);
    } catch (error) {
      if (!accepts(requestId, requestedTarget)) return;
      setIssue(
        error instanceof Error
          ? error.message
          : '工作人员当前绑定读取失败，请稍后重试',
      );
    } finally {
      if (accepts(requestId, requestedTarget)) setLoading(false);
    }
  };

  const currentUserBinding = userLookup?.currentMiniappBinding;
  const currentStaffBinding = staffBinding?.currentMiniappBinding;
  const sameBinding = !!selectedStaffUid
    && currentUserBinding?.staffAccountUid === selectedStaffUid
    && currentStaffBinding?.organizationUserUid === user.organizationUserUid;
  const willReplaceBinding = !!selectedStaffUid && !sameBinding && (
    !!currentUserBinding || !!currentStaffBinding
  );
  const selectedStaffEnabled = staffOptions.some(
    (option) => option.value === selectedStaffUid && !option.disabled,
  );
  const snapshotsReady = !!userLookup
    && !!selectedStaffUid
    && staffBindingOwner === selectedStaffUid
    && !!staffBinding;

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
      await onChanged?.();
      await reload(selectedStaffUid);
    } catch (error) {
      if (error instanceof ApiProblem && error.isVersionConflict) {
        await reload(selectedStaffUid);
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
      message.success('该机构用户的工作人员绑定已撤销');
      await onChanged?.();
      await reload();
    } catch (error) {
      if (error instanceof ApiProblem && error.isVersionConflict) {
        await reload();
        message.warning('绑定关系已经变化，已载入最新状态；请核对后重新确认');
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (!canBind) return null;

  return (
    <Card
      size="small"
      title={(
        <Space>
          <LinkOutlined />
          <span>绑定工作人员</span>
        </Space>
      )}
      style={{ marginTop: 16 }}
    >
      <Typography.Paragraph type="secondary">
        选择现有工作人员后，系统会同时核对用户侧和工作人员侧的最新绑定版本。
        如任一侧已有绑定，将以原子方式完成换绑，并撤销相关旧会话。
      </Typography.Paragraph>
      {!phoneNumber ? (
        <Alert
          showIcon
          type="warning"
          message="用户尚未绑定手机号，暂时不能绑定工作人员"
          description="请先让用户在当前机构小程序完成手机号授权，再回到这里操作。"
        />
      ) : (
        <Spin spinning={loading}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {issue && <Alert showIcon type="error" message={issue} />}
            <Select
              aria-label="绑定工作人员"
              showSearch
              optionFilterProp="label"
              placeholder="选择要绑定的工作人员"
              value={selectedStaffUid}
              options={staffOptions}
              onChange={(value) => void selectStaff(value)}
              style={{ width: '100%' }}
            />

            {userLookup && (
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="机构用户当前绑定">
                  {currentUserBinding
                    ? (
                        <Space wrap>
                          <Tag color="processing">已绑定</Tag>
                          <Typography.Text copyable>
                            {currentUserBinding.staffAccountUid}
                          </Typography.Text>
                          <Typography.Text type="secondary">
                            v{currentUserBinding.version}
                          </Typography.Text>
                        </Space>
                      )
                    : '无'}
                </Descriptions.Item>
                <Descriptions.Item label="所选工作人员当前绑定">
                  {!selectedStaffUid || staffBindingOwner !== selectedStaffUid
                    ? '请选择工作人员并等待读取'
                    : currentStaffBinding
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
                description="用户或所选工作人员当前已绑定其他对象；提交时会再次核对两侧版本，不会静默覆盖并发变更。"
              />
            )}
            {sameBinding && (
              <Alert
                showIcon
                type="success"
                icon={<SafetyCertificateOutlined />}
                message="该用户已绑定到所选工作人员"
              />
            )}
            {!staffOptions.length && !loading && (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="当前租户没有可绑定的工作人员账号"
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
            <Space wrap>
              <Popconfirm
                title={willReplaceBinding ? '确认替换现有绑定？' : '确认绑定该工作人员？'}
                description="提交成功后，相关旧小程序会话会立即失效。"
                onConfirm={() => void bind()}
              >
                <Button
                  type="primary"
                  disabled={
                    !snapshotsReady
                    || !selectedStaffEnabled
                    || sameBinding
                    || !!issue
                  }
                  loading={submitting}
                >
                  {willReplaceBinding ? '确认原子换绑' : '确认绑定'}
                </Button>
              </Popconfirm>
              {currentUserBinding && (
                <Popconfirm
                  title="解除该机构用户的工作人员绑定？"
                  description="当前工作人员的管理小程序会话将立即失效。"
                  onConfirm={() => void revoke()}
                >
                  <Button danger loading={submitting}>
                    解除当前绑定
                  </Button>
                </Popconfirm>
              )}
            </Space>
          </Space>
        </Spin>
      )}
    </Card>
  );
}
