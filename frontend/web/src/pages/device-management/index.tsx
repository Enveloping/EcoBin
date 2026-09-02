import { useEffect, useMemo, useRef, useState } from 'react';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  AppstoreAddOutlined,
  ArrowRightOutlined,
  DashboardOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import {
  assignPlatformDeviceTenant,
  assignTenantDeviceOrganization,
  createPlatformDeviceAsset,
  disablePlatformDevice,
  listOrganizationDevices,
  listPlatformDeviceAssets,
  listTenantDeviceAssets,
  reevaluateDeviceAcceptance,
  restorePlatformDevice,
  retirePlatformDevice,
  type CreateDeviceAssetRequest,
  type DeviceAsset,
} from '@/api/deviceDirectory';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { useAuthStore } from '@/stores/authStore';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import DeviceAssetDrawer, {
  type DeviceControlKind,
  type DeviceManagementMode,
} from './DeviceAssetDrawer';
import RuntimeSnapshotPolicyModal from './RuntimeSnapshotPolicyModal';
import { operatorErrorMessage } from './operatorErrorPresentation';
import {
  acceptanceColors,
  acceptanceLabels,
  assetColors,
  assetLabels,
  connectivityColors,
  connectivityLabels,
} from './devicePresentation';
import {
  businessAdmissionPresentation,
  deviceManagementSummary,
} from './deviceManagementPresentation';

interface AssetFormValues {
  hardwareSn: string;
  modelCode: string;
  productionBatch?: string;
  expectedPortCount: number;
}

interface AssignmentFormValues {
  targetCode: string;
}

interface ControlFormValues {
  reason: string;
}

interface AssignmentState {
  kind: 'tenant' | 'organization';
  asset: DeviceAsset;
}

interface ControlState {
  kind: DeviceControlKind;
  asset: DeviceAsset;
}

function errorMessage(error: unknown): string {
  return operatorErrorMessage(error, '设备操作未完成，请刷新页面后再试');
}

const controlCopy: Record<
  DeviceControlKind,
  { title: string; action: string; warning: string }
> = {
  disable: {
    title: '禁用设备',
    action: '确认禁用',
    warning: '禁用后租户和机构立即看不到设备，也不能开始新业务；已创建作业继续收敛。',
  },
  restore: {
    title: '恢复设备',
    action: '确认恢复',
    warning: '恢复后系统仍会重新检查设备联网、配置、空袋重量和安全状态，不会跳过任何必要条件。',
  },
  retire: {
    title: '报废设备',
    action: '永久报废',
    warning: '报废不可恢复，永久归属和历史业务仍保留，但设备永远不能开始新业务。',
  },
};

export default function DeviceManagementPage() {
  const directoryScope = useDirectoryScope();
  const organizationScope = useOrganizationScope(directoryScope);
  const accountType = useAuthStore((state) => state.session?.accountType);
  const hasCapability = useAuthStore((state) => state.hasCapability);
  const executeCommand = useCommandExecutor();
  const actionRef = useRef<ActionType>(null);
  const [assetForm] = Form.useForm<AssetFormValues>();
  const [assignmentForm] = Form.useForm<AssignmentFormValues>();
  const [controlForm] = Form.useForm<ControlFormValues>();
  const [selected, setSelected] = useState<DeviceAsset>();
  const [assetModalOpen, setAssetModalOpen] = useState(false);
  const [runtimePolicyOpen, setRuntimePolicyOpen] = useState(false);
  const [assignment, setAssignment] = useState<AssignmentState>();
  const [control, setControl] = useState<ControlState>();
  const [submitting, setSubmitting] = useState(false);

  const platform = directoryScope.platform;
  const tenantManager = !platform && (
    accountType === 'TENANT_PRINCIPAL'
    || hasCapability('device.assignment.manage')
  );
  const mode: DeviceManagementMode = platform
    ? 'platform'
    : tenantManager
      ? 'tenant'
      : 'organization';
  const canCreate = platform && hasCapability('device.manage');

  useEffect(() => {
    const refreshVisibleList = () => {
      if (document.visibilityState === 'visible') {
        void actionRef.current?.reload();
      }
    };
    const interval = window.setInterval(refreshVisibleList, 15_000);
    document.addEventListener('visibilitychange', refreshVisibleList);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', refreshVisibleList);
    };
  }, [mode, organizationScope.organizationCode]);

  const pageCopy = mode === 'platform'
    ? {
      title: '永久设备资产',
      description: '平台登记物理设备资产、查看当前在线状态和历史检查记录，并只分配一次租户。',
    }
    : mode === 'tenant'
      ? {
        title: '租户设备',
        description: '这里只展示当前可管理的永久资产；选定机构后归属不能再修改。',
      }
      : {
        title: '机构设备',
        description: '机构无需手动启用设备；系统会结合联网、配置、安全、占用和软件状态判断能否开始投递或清运。',
      };

  const columns = useMemo<ProColumns<DeviceAsset>[]>(() => [
    {
      title: '设备',
      dataIndex: 'hardwareSn',
      width: 250,
      fieldProps: { placeholder: '搜索设备序列号' },
      render: (_, asset) => (
        <Space direction="vertical" size={1}>
          <Typography.Link
            strong
            onClick={() => setSelected(asset)}
          >
            {asset.hardwareSn}
          </Typography.Link>
          <Typography.Text type="secondary" copyable>
            设备编号：{asset.deviceCode}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '联网状态',
      dataIndex: ['connectivity', 'oneNetConnectionStatus'],
      search: false,
      width: 170,
      render: (_, asset) => {
        const status = asset.connectivity?.oneNetConnectionStatus ?? 'UNKNOWN';
        return (
          <Space direction="vertical" size={1}>
            <Tag color={connectivityColors[status]}>
              {connectivityLabels[status]}
            </Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {asset.connectivity?.statusObservedAt
                ? formatShanghaiTime(asset.connectivity.statusObservedAt)
                : '平台尚未收到设备联网状态'}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: '新业务状态',
      dataIndex: ['deviceManagement', 'businessAdmission'],
      search: false,
      width: 240,
      render: (_, asset) => {
        const management = deviceManagementSummary(asset);
        const presentation = businessAdmissionPresentation(
          management,
        );
        const secondary = management?.primaryReason?.title
          ?? (management?.architectureGeneration === 'PERMANENT_V1'
            ? '打开设备详情可查看当前判断依据'
            : '仍按联网、配置、安全和占用等现有条件检查');
        return (
          <Space direction="vertical" size={1}>
            <Tag color={presentation.color}>{presentation.label}</Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {secondary}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: '型号 / 投口',
      dataIndex: 'modelCode',
      search: false,
      render: (_, asset) => (
        <span>{asset.modelCode} · {asset.expectedPortCount} 口</span>
      ),
    },
    {
      title: '永久归属',
      dataIndex: 'tenantCode',
      search: false,
      render: (_, asset) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>
            {asset.tenantCode ?? '尚未分配租户'}
          </Typography.Text>
          <Typography.Text type="secondary">
            {asset.organizationCode ?? '尚未分配机构'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '设备功能检查',
      dataIndex: 'acceptanceStatus',
      valueType: 'select',
      hideInSearch: mode !== 'platform',
      valueEnum: Object.fromEntries(
        Object.entries(acceptanceLabels).map(([key, text]) => [key, { text }]),
      ),
      render: (_, asset) => (
        <Tag color={acceptanceColors[asset.acceptanceStatus]}>
          {acceptanceLabels[asset.acceptanceStatus]}
        </Tag>
      ),
    },
    {
      title: '生命周期',
      dataIndex: 'lifecycleStatus',
      valueType: 'select',
      hideInSearch: mode !== 'platform',
      valueEnum: Object.fromEntries(
        Object.entries(assetLabels).map(([key, text]) => [key, { text }]),
      ),
      render: (_, asset) => (
        <Tag color={assetColors[asset.lifecycleStatus]}>
          {assetLabels[asset.lifecycleStatus]}
        </Tag>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updatedAt',
      search: false,
      width: 180,
      render: (_, asset) => formatShanghaiTime(asset.updatedAt),
    },
    {
      title: '',
      valueType: 'option',
      width: 70,
      render: (_, asset) => (
        <Button
          type="text"
          icon={<ArrowRightOutlined />}
          aria-label={`查看设备 ${asset.hardwareSn}`}
          onClick={() => setSelected(asset)}
        />
      ),
    },
  ], [mode]);

  const reload = () => actionRef.current?.reload();

  const createAsset = async () => {
    const values = await assetForm.validateFields();
    const payload: CreateDeviceAssetRequest = {
      hardwareSn: values.hardwareSn.trim(),
      modelCode: values.modelCode.trim(),
      productionBatch: values.productionBatch?.trim() || null,
      expectedPortCount: values.expectedPortCount,
    };
    setSubmitting(true);
    try {
      const created = await executeCommand(
        commandKey('device.asset.create', payload.hardwareSn, payload),
        (intent) => createPlatformDeviceAsset(payload, intent),
      );
      message.success('设备资产已创建，设备联网并完成初始袋登记后，系统会自动验收');
      setAssetModalOpen(false);
      assetForm.resetFields();
      setSelected(created);
      reload();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const submitAssignment = async () => {
    if (!assignment) return;
    const { targetCode } = await assignmentForm.validateFields();
    setSubmitting(true);
    try {
      const payload = assignment.kind === 'tenant'
        ? { tenantCode: targetCode, expectedVersion: assignment.asset.version }
        : { organizationCode: targetCode, expectedVersion: assignment.asset.version };
      const updated = await executeCommand(
        commandKey(
          `device.asset.assign-${assignment.kind}`,
          assignment.asset.hardwareSn,
          payload,
        ),
        (intent) => assignment.kind === 'tenant'
          ? assignPlatformDeviceTenant(
            assignment.asset.hardwareSn,
            payload as { tenantCode: string; expectedVersion: number },
            intent,
          )
          : assignTenantDeviceOrganization(
            assignment.asset.hardwareSn,
            payload as { organizationCode: string; expectedVersion: number },
            intent,
          ),
      );
      message.success(
        assignment.kind === 'tenant'
          ? '租户永久归属已写入'
          : '机构永久归属已写入，设备联网后系统会自动检查业务条件',
      );
      setAssignment(undefined);
      assignmentForm.resetFields();
      setSelected(updated);
      reload();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const submitControl = async () => {
    if (!control) return;
    const { reason } = await controlForm.validateFields();
    const payload = {
      expectedVersion: control.asset.version,
      reason: reason.trim(),
    };
    setSubmitting(true);
    try {
      const updated = await executeCommand(
        commandKey(
          `device.asset.${control.kind}`,
          control.asset.hardwareSn,
          payload,
        ),
        (intent) => {
          if (control.kind === 'disable') {
            return disablePlatformDevice(
              control.asset.hardwareSn, payload, intent,
            );
          }
          if (control.kind === 'restore') {
            return restorePlatformDevice(
              control.asset.hardwareSn, payload, intent,
            );
          }
          return retirePlatformDevice(
            control.asset.hardwareSn, payload, intent,
          );
        },
      );
      message.success(`${controlCopy[control.kind].title}成功`);
      setControl(undefined);
      controlForm.resetFields();
      setSelected(updated);
      reload();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={pageHeader(pageCopy.title, pageCopy.description)}
      extra={canCreate ? [
        <Button
          key="runtime-policy"
          icon={<DashboardOutlined />}
          onClick={() => setRuntimePolicyOpen(true)}
        >
          设备状态上报策略
        </Button>,
        <Button
          key="create"
          type="primary"
          icon={<AppstoreAddOutlined />}
          onClick={() => {
            assetForm.setFieldsValue({
              expectedPortCount: 1,
            });
            setAssetModalOpen(true);
          }}
        >
          登记真实设备
        </Button>,
      ] : undefined}
    >
      <Alert
        type="info"
        showIcon
        icon={<SafetyCertificateOutlined />}
        message="永久归属 · 自动验收 · 自动检查业务条件"
        description={
          mode === 'platform'
            ? '设备功能检查发生在分配租户之前；设备控制板和摄像头是否为模拟来源只用于诊断，平台依据联网、通信、采集和上传等实际检查结果自动判定。'
            : mode === 'tenant'
              ? '租户界面不展示安装或启用进度；设备只能永久分配一次机构。'
              : '系统自动下发配置、重测厂家初始袋皮重并计算业务资格，不需要现场确认或经营开关。'
        }
        style={{ marginBottom: 16, borderLeft: '4px solid #1677ff' }}
      />

      {mode === 'organization' && !organizationScope.organizationCode ? (
        <Alert
          type="warning"
          showIcon
          message="请选择机构后查看设备"
        />
      ) : (
        <ProTable<DeviceAsset>
          {...proTableConfig}
          actionRef={actionRef}
          rowKey="assetUid"
          columns={columns}
          request={async (params) => {
            try {
              const query = {
                page: params.current,
                pageSize: params.pageSize,
                hardwareSn:
                  typeof params.hardwareSn === 'string'
                    ? params.hardwareSn.trim() || undefined
                    : undefined,
              };
              const page = mode === 'platform'
                ? await listPlatformDeviceAssets({
                  ...query,
                  lifecycleStatus:
                    typeof params.lifecycleStatus === 'string'
                      ? params.lifecycleStatus as DeviceAsset['lifecycleStatus']
                      : undefined,
                  acceptanceStatus:
                    typeof params.acceptanceStatus === 'string'
                      ? params.acceptanceStatus as DeviceAsset['acceptanceStatus']
                      : undefined,
                })
                : mode === 'tenant'
                  ? await listTenantDeviceAssets(query)
                  : await listOrganizationDevices(
                    organizationScope.organizationCode!,
                    query,
                  );
              return {
                data: page.items,
                total: page.total,
                success: true,
              };
            } catch (error) {
              message.error(errorMessage(error));
              return { data: [], total: 0, success: false };
            }
          }}
          search={{ labelWidth: 'auto' }}
          pagination={{ defaultPageSize: 20, showSizeChanger: true }}
        />
      )}

      <DeviceAssetDrawer
        open={Boolean(selected)}
        mode={mode}
        asset={selected}
        organizationCode={organizationScope.organizationCode}
        onClose={() => setSelected(undefined)}
        onAssignTenant={(asset) => {
          assignmentForm.resetFields();
          setAssignment({ kind: 'tenant', asset });
        }}
        onAssignOrganization={(asset) => {
          assignmentForm.resetFields();
          setAssignment({ kind: 'organization', asset });
        }}
        onControl={(asset, kind) => {
          controlForm.resetFields();
          setControl({ asset, kind });
        }}
        onReevaluateAcceptance={async (asset) => {
          const updated = await executeCommand(
            commandKey(
              'device.acceptance.reevaluate',
              asset.hardwareSn,
              {},
            ),
            (intent) => reevaluateDeviceAcceptance(
              asset.hardwareSn, intent,
            ),
          );
          setSelected(updated);
          reload();
          message.success('已根据最新设备检查记录重新核对验收结果');
        }}
        onChanged={reload}
      />

      <RuntimeSnapshotPolicyModal
        open={runtimePolicyOpen}
        onClose={() => setRuntimePolicyOpen(false)}
      />

      <Modal
        title="登记真实设备资产"
        open={assetModalOpen}
        confirmLoading={submitting}
        okText="创建资产"
        onOk={() => void createAsset()}
        onCancel={() => setAssetModalOpen(false)}
      >
        <Alert
          type="warning"
          showIcon
          message="这里只登记设备资产，不登记厂家初始袋"
          description="设备序列号创建后不可替换。设备首次装袋必须由已绑定的厂家操作员在共享小程序的设备出厂端逐口扫描防伪袋码（EB1 格式）；所有投口完成装袋后，设备才会进入自动验收。"
          style={{ marginBottom: 20 }}
        />
        <Form form={assetForm} layout="vertical">
          <Form.Item
            name="hardwareSn"
            label="设备序列号 / 物联网平台设备名称"
            rules={[{ required: true }, {
              pattern: /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/,
              message: '请输入 1～64 位合法设备序列号',
            }]}
          >
            <Input maxLength={64} />
          </Form.Item>
          <Space size={16} align="start" style={{ width: '100%' }}>
            <Form.Item
              name="modelCode"
              label="设备型号"
              rules={[{ required: true }]}
              style={{ flex: 1 }}
            >
              <Input maxLength={100} />
            </Form.Item>
            <Form.Item
              name="productionBatch"
              label="生产批次"
              style={{ flex: 1 }}
            >
              <Input maxLength={64} />
            </Form.Item>
            <Form.Item
              name="expectedPortCount"
              label="投口数量"
              rules={[{ required: true }]}
            >
              <InputNumber
                min={1}
                max={6}
                precision={0}
              />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal
        title={assignment?.kind === 'tenant' ? '永久分配租户' : '永久分配机构'}
        open={Boolean(assignment)}
        confirmLoading={submitting}
        okText="确认永久归属"
        onOk={() => void submitAssignment()}
        onCancel={() => setAssignment(undefined)}
      >
        <Alert
          type="warning"
          showIcon
          message="该归属写入后不能修改、清空或调拨"
          description="如果选错，只能由平台报废设备，不能把同一台机器改给其他主体。"
          style={{ marginBottom: 20 }}
        />
        <Form form={assignmentForm} layout="vertical">
          <Form.Item
            name="targetCode"
            label={assignment?.kind === 'tenant' ? '目标租户' : '目标机构'}
            rules={[{ required: true, message: '请选择目标主体' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              options={
                assignment?.kind === 'tenant'
                  ? directoryScope.tenantOptions
                  : organizationScope.organizationOptions
              }
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={control ? controlCopy[control.kind].title : '设备控制'}
        open={Boolean(control)}
        confirmLoading={submitting}
        okText={control ? controlCopy[control.kind].action : '确认'}
        okButtonProps={{ danger: control?.kind === 'retire' }}
        onOk={() => void submitControl()}
        onCancel={() => setControl(undefined)}
      >
        {control && (
          <Alert
            type={control.kind === 'retire' ? 'error' : 'warning'}
            showIcon
            message={controlCopy[control.kind].warning}
            style={{ marginBottom: 20 }}
          />
        )}
        <Form form={controlForm} layout="vertical">
          <Form.Item
            name="reason"
            label="原因"
            rules={[{ required: true, message: '请填写原因' }]}
          >
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
