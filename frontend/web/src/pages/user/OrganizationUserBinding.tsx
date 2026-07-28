import { useEffect, useMemo, useState } from 'react';
import {
  CheckCircleFilled,
  LinkOutlined,
  MobileOutlined,
  SafetyCertificateOutlined,
  UserSwitchOutlined,
} from '@ant-design/icons';
import { PageContainer, ProCard } from '@ant-design/pro-components';
import {
  Alert,
  App,
  Button,
  Descriptions,
  Divider,
  Empty,
  Input,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  getStaffMiniappBinding,
  listOrganizations,
  listStaffAccounts,
  lookupOrganizationUserByPhone,
  revokeStaffMiniappBinding,
  setStaffMiniappBinding,
  type OrganizationUserLookup,
  type StaffMiniappBindingLookup,
} from '@/api/identityDirectory';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useAuthStore } from '@/stores/authStore';
import { pageHeader } from '@/utils/pageStyle';

export default function OrganizationUserBindingPage() {
  const scope = useDirectoryScope();
  const { message } = App.useApp();
  const canBind = useAuthStore((state) =>
    state.hasCapability('staff.bind'));
  const [organizationCode, setOrganizationCode] = useState<string>();
  const [staffUid, setStaffUid] = useState<string>();
  const [phoneNumber, setPhoneNumber] = useState('');
  const [reason, setReason] = useState('');
  const [organizationOptions, setOrganizationOptions] = useState<
    Array<{ label: string; value: string }>
  >([]);
  const [staffOptions, setStaffOptions] = useState<
    Array<{ label: string; value: string }>
  >([]);
  const [user, setUser] = useState<OrganizationUserLookup | null>(null);
  const [staffBinding, setStaffBinding] =
    useState<StaffMiniappBindingLookup | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [lookingUp, setLookingUp] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setOrganizationCode(undefined);
    setStaffUid(undefined);
    setUser(null);
    setStaffBinding(null);
    if (!scope.context) {
      setOrganizationOptions([]);
      setStaffOptions([]);
      return;
    }
    let active = true;
    setLoadingOptions(true);
    Promise.all([
      listOrganizations(scope.context, { page: 1, pageSize: 200 }),
      listStaffAccounts(scope.context, { page: 1, pageSize: 200 }),
    ])
      .then(([organizations, staff]) => {
        if (!active) return;
        const organizationsNext = organizations.items.map((item) => ({
          value: item.organizationCode,
          label: `${item.organizationName} · ${item.organizationCode}`,
        }));
        setOrganizationOptions(organizationsNext);
        setOrganizationCode(organizationsNext[0]?.value);
        setStaffOptions(staff.items
          .filter((item) => item.status === 'ENABLED')
          .map((item) => ({
            value: item.staffAccountUid,
            label: `${item.displayName} · ${item.loginName}`,
          })));
      })
      .finally(() => active && setLoadingOptions(false));
    return () => {
      active = false;
    };
  }, [scope.context]);

  const selectionReady = !!scope.context
    && !!organizationCode
    && !!staffUid
    && !!phoneNumber.trim();

  const snapshots = useMemo(() => ({
    expectedStaffBinding: staffBinding?.currentMiniappBinding
      ? {
          bindingUid:
            staffBinding.currentMiniappBinding.bindingUid,
          version: staffBinding.currentMiniappBinding.version,
        }
      : null,
    expectedOrganizationUserBinding: user?.currentMiniappBinding
      ? {
          bindingUid: user.currentMiniappBinding.bindingUid,
          version: user.currentMiniappBinding.version,
        }
      : null,
  }), [staffBinding, user]);

  const lookup = async () => {
    if (!selectionReady || !scope.context || !organizationCode || !staffUid) {
      message.warning('请先选择机构、工作人员并填写完整手机号');
      return;
    }
    setLookingUp(true);
    try {
      const [foundUser, foundStaffBinding] = await Promise.all([
        lookupOrganizationUserByPhone(
          scope.context, organizationCode, phoneNumber.trim()),
        getStaffMiniappBinding(
          scope.context, organizationCode, staffUid),
      ]);
      setUser(foundUser);
      setStaffBinding(foundStaffBinding);
      message.success('已取得两侧最新绑定快照');
    } catch {
      setUser(null);
      setStaffBinding(null);
    } finally {
      setLookingUp(false);
    }
  };

  const bind = async () => {
    if (!scope.context || !organizationCode || !staffUid || !user) return;
    setSubmitting(true);
    try {
      await setStaffMiniappBinding(
        scope.context,
        organizationCode,
        staffUid,
        {
          organizationUserUid: user.organizationUserUid,
          ...snapshots,
          reason: reason.trim() || undefined,
        },
      );
      message.success('绑定已生效，相关旧会话已撤销');
      await lookup();
    } finally {
      setSubmitting(false);
    }
  };

  const revoke = async () => {
    const binding = staffBinding?.currentMiniappBinding;
    if (!scope.context || !organizationCode || !binding) return;
    setSubmitting(true);
    try {
      await revokeStaffMiniappBinding(
        scope.context,
        organizationCode,
        binding,
        reason.trim() || undefined,
      );
      message.success('工作人员小程序绑定已撤销');
      await lookup();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      {...pageHeader(
        '用户与工作人员绑定',
        '精确手机号核验 · 双侧版本快照 · 原子换绑',
      )}
    >
      <DirectoryScopeBar scope={scope} />
      {!canBind && (
        <Alert
          showIcon
          type="info"
          message="当前账号可核验机构用户，但没有 staff.bind，绑定操作保持只读。"
          style={{ marginBottom: 16 }}
        />
      )}
      <ProCard
        bordered
        style={{ borderTop: '3px solid #1677ff' }}
        title={(
          <Space>
            <SafetyCertificateOutlined />
            <span>人工身份核验台</span>
          </Space>
        )}
        extra={<Tag color="blue">完整手机号不进入 URL</Tag>}
      >
        <Space wrap size={12} style={{ width: '100%' }}>
          <Select
            aria-label="机构"
            loading={loadingOptions}
            value={organizationCode}
            options={organizationOptions}
            placeholder="选择机构"
            style={{ width: 300 }}
            onChange={(value) => {
              setOrganizationCode(value);
              setUser(null);
              setStaffBinding(null);
            }}
          />
          <Select
            aria-label="工作人员"
            showSearch
            optionFilterProp="label"
            loading={loadingOptions}
            value={staffUid}
            options={staffOptions}
            placeholder="选择工作人员"
            style={{ width: 300 }}
            onChange={(value) => {
              setStaffUid(value);
              setUser(null);
              setStaffBinding(null);
            }}
          />
          <Input
            aria-label="完整手机号"
            prefix={<MobileOutlined />}
            value={phoneNumber}
            placeholder="输入完整手机号精确核验"
            maxLength={24}
            style={{ width: 260 }}
            onChange={(event) => {
              setPhoneNumber(event.target.value);
              setUser(null);
              setStaffBinding(null);
            }}
            onPressEnter={lookup}
          />
          <Button
            type="primary"
            icon={<UserSwitchOutlined />}
            loading={lookingUp}
            disabled={!selectionReady}
            onClick={lookup}
          >
            取得双侧快照
          </Button>
        </Space>
      </ProCard>

      <ProCard gutter={16} style={{ marginTop: 16 }}>
        <ProCard
          colSpan="50%"
          bordered
          title="手机号对应的机构用户"
          extra={user && (
            <Tag color={user.status === 'ACTIVE' ? 'green' : 'orange'}>
              {user.status}
            </Tag>
          )}
        >
          {!user ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="等待精确手机号核验"
            />
          ) : (
            <Descriptions column={1} size="small">
              <Descriptions.Item label="昵称">
                {user.nickname}
              </Descriptions.Item>
              <Descriptions.Item label="手机号">
                {user.maskedPhoneNumber}
              </Descriptions.Item>
              <Descriptions.Item label="用户 UID">
                <Typography.Text copyable>
                  {user.organizationUserUid}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="当前绑定">
                {user.currentMiniappBinding
                  ? `工作人员 ${user.currentMiniappBinding.staffAccountUid} · v${user.currentMiniappBinding.version}`
                  : '无'}
              </Descriptions.Item>
            </Descriptions>
          )}
        </ProCard>
        <ProCard
          colSpan="50%"
          bordered
          title="工作人员当前小程序绑定"
          extra={staffBinding && (
            <Tag color={
              staffBinding.currentMiniappBinding ? 'processing' : 'default'
            }>
              {staffBinding.currentMiniappBinding ? '已绑定' : '未绑定'}
            </Tag>
          )}
        >
          {!staffBinding ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="等待读取工作人员快照"
            />
          ) : staffBinding.currentMiniappBinding ? (
            <Descriptions column={1} size="small">
              <Descriptions.Item label="机构用户">
                {staffBinding.currentMiniappBinding.nickname
                  ?? staffBinding.currentMiniappBinding.organizationUserUid}
              </Descriptions.Item>
              <Descriptions.Item label="手机号">
                {staffBinding.currentMiniappBinding.maskedPhoneNumber ?? '无 user.read 权限'}
              </Descriptions.Item>
              <Descriptions.Item label="绑定版本">
                v{staffBinding.currentMiniappBinding.version}
              </Descriptions.Item>
            </Descriptions>
          ) : (
            <Alert showIcon type="success" message="该工作人员当前没有活动绑定" />
          )}
        </ProCard>
      </ProCard>

      <ProCard
        bordered
        style={{ marginTop: 16 }}
        title={(
          <Space>
            <LinkOutlined />
            <span>提交原子绑定</span>
          </Space>
        )}
      >
        <Typography.Paragraph type="secondary">
          提交时会再次锁定并核对上方两侧快照；任何一侧变化都会返回冲突，
          不会静默覆盖。换绑成功后，旧管理会话和目标用户普通会话立即失效。
        </Typography.Paragraph>
        <Input.TextArea
          aria-label="绑定原因"
          value={reason}
          maxLength={500}
          showCount
          autoSize={{ minRows: 2, maxRows: 4 }}
          placeholder="原因（可选，进入安全审计）"
          onChange={(event) => setReason(event.target.value)}
        />
        <Divider />
        <Space>
          <Button
            type="primary"
            icon={<CheckCircleFilled />}
            disabled={!canBind || !user || !staffBinding}
            loading={submitting}
            onClick={bind}
          >
            确认绑定 / 原子换绑
          </Button>
          {staffBinding?.currentMiniappBinding && (
            <Popconfirm
              title="撤销该工作人员的小程序绑定？"
              description="已有管理会话会立即失效。"
              onConfirm={revoke}
            >
              <Button danger loading={submitting} disabled={!canBind}>
                撤销当前绑定
              </Button>
            </Popconfirm>
          )}
        </Space>
      </ProCard>
    </PageContainer>
  );
}
