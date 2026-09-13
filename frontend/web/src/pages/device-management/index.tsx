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
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  AppstoreAddOutlined,
  ArrowRightOutlined,
  DashboardOutlined,
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
  assetColors,
  assetLabels,
  connectivityColors,
  connectivityLabels,
} from './devicePresentation';
import { DeviceFaults, DeviceIdentifier, DevicePortMetric, listAcceptanceLabels } from './DeviceListCells';

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
    warning: '有投递、清运或远程维护时不能禁用。配置和更新任务暂停保留，其他现场任务结束，历史记录保留。',
  },
  restore: {
    title: '恢复设备',
    action: '确认恢复',
    warning: '恢复后继续处理保留的配置和更新任务；设备离线时等待上线。系统仍会检查当前配置、空袋重量和安全状态。',
  },
  retire: {
    title: '报废设备',
    action: '永久报废',
    warning: '有投递、清运或远程维护时不能报废。报废将取消待处理的设备任务，历史记录保留，设备不可恢复。',
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
      order: 3,
      width: 125,
      fieldProps: { placeholder: '搜索设备序列号' },
      render: (_, asset) => (
        <DeviceIdentifier asset={asset} onOpen={() => setSelected(asset)} />
      ),
    },
    {
      title: '联网状态',
      dataIndex: ['connectivity', 'oneNetConnectionStatus'],
      search: false,
      width: 90,
      render: (_, asset) => {
        const status = asset.connectivity?.oneNetConnectionStatus ?? 'UNKNOWN';
        return <Tooltip title={asset.connectivity?.statusObservedAt
          ? `最近更新：${formatShanghaiTime(asset.connectivity.statusObservedAt)}` : '尚未收到联网状态'}>
          <Tag color={connectivityColors[status]}>{connectivityLabels[status]}</Tag>
        </Tooltip>;
      },
    },
    {
      title: '故障原因', key: 'faults', search: false, width: 180,
      render: (_, asset) => <DeviceFaults asset={asset} />,
    },
    {
      title: '投口重量', key: 'portWeight', search: false, width: 120,
      render: (_, asset) => <DevicePortMetric asset={asset} metric="weight" />,
    },
    {
      title: '重量满溢', key: 'weightFull', search: false, width: 90,
      render: (_, asset) => <DevicePortMetric asset={asset} metric="weightFull" />,
    },
    {
      title: '红外满溢', key: 'infraredFull', search: false, width: 90,
      render: (_, asset) => <DevicePortMetric asset={asset} metric="infraredFull" />,
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
      title: '归属',
      width: 115,
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
      title: '出厂验收',
      dataIndex: 'acceptanceStatus',
      order: 1,
      valueType: 'select',
      hideInSearch: mode !== 'platform',
      valueEnum: Object.fromEntries(
        Object.entries(listAcceptanceLabels).map(([key, text]) => [key, { text }]),
      ),
      render: (_, asset) => (
        <Tag color={acceptanceColors[asset.acceptanceStatus]}>
          {listAcceptanceLabels[asset.acceptanceStatus]}
        </Tag>
      ),
    },
    {
      title: '设备状态',
      dataIndex: 'lifecycleStatus',
      order: 2,
      valueType: 'select',
      fieldProps: { placeholder: '未报废' },
      valueEnum: { ALL: { text: '全部' }, ...Object.fromEntries(
        Object.entries(assetLabels).map(([key, text]) => [key, { text }]),
      ) },
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
      width: 125,
      render: (_, asset) => <Tooltip title={formatShanghaiTime(asset.updatedAt)}>
        {formatShanghaiTime(asset.updatedAt).slice(5, 16)}
      </Tooltip>,
    },
    {
      title: '',
      valueType: 'option',
      fixed: 'right',
      width: 45,
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
      {...pageHeader(pageCopy.title)}
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
          columnsState={{
            persistenceKey: `ecobin.web.columns.devices.${mode}.v1`,
            persistenceType: 'localStorage',
            defaultValue: {
              modelCode: { show: false },
              acceptanceStatus: { show: false },
              lifecycleStatus: { show: false },
            },
          }}
          request={async (params) => {
            try {
              const query = {
                page: params.current,
                pageSize: params.pageSize,
                lifecycleStatus: typeof params.lifecycleStatus === 'string'
                  ? params.lifecycleStatus as DeviceAsset['lifecycleStatus'] | 'ALL' : undefined,
                hardwareSn:
                  typeof params.hardwareSn === 'string'
                    ? params.hardwareSn.trim() || undefined
                    : undefined,
              };
              const page = mode === 'platform'
                ? await listPlatformDeviceAssets({
                  ...query,
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
          message="设备序列号登记后不可修改，请核对机身编号。"
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
