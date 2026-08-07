import { useMemo, useRef, useState } from 'react';
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
import { ApiProblem } from '@/api/request';
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
import {
  acceptanceColors,
  acceptanceLabels,
  assetColors,
  assetLabels,
} from './devicePresentation';

interface AssetFormValues {
  hardwareSn: string;
  modelCode: string;
  productionBatch?: string;
  expectedPortCount: number;
  factoryBags: Array<{ bagCode: string }>;
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
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '设备操作失败';
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
    warning: '恢复只重新参加实时准入判断，不会跳过联网、配置、皮重或安全条件。',
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
  const [assignment, setAssignment] = useState<AssignmentState>();
  const [control, setControl] = useState<ControlState>();
  const [submitting, setSubmitting] = useState(false);
  const [portCount, setPortCount] = useState(1);

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

  const pageCopy = mode === 'platform'
    ? {
      title: '永久设备资产',
      description: '平台登记真实机器、查看自动验收证据，并只分配一次租户。',
    }
    : mode === 'tenant'
      ? {
        title: '租户设备',
        description: '这里只展示当前可管理的永久资产；选定机构后归属不能再修改。',
      }
      : {
        title: '机构设备',
        description: '安装、通电、联网即可使用；本页只查看设备并管理日常价格与配置。',
      };

  const columns = useMemo<ProColumns<DeviceAsset>[]>(() => [
    {
      title: '设备',
      dataIndex: 'hardwareSn',
      width: 250,
      fieldProps: { placeholder: '搜索硬件 SN' },
      render: (_, asset) => (
        <Space direction="vertical" size={1}>
          <Typography.Link
            strong
            onClick={() => setSelected(asset)}
          >
            {asset.hardwareSn}
          </Typography.Link>
          <Typography.Text type="secondary" copyable>
            {asset.deviceCode}
          </Typography.Text>
        </Space>
      ),
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
      title: '机器验收',
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
      factoryBags: values.factoryBags
        .slice(0, values.expectedPortCount)
        .map((bag, index) => ({
          portNo: index + 1,
          bagCode: bag.bagCode.trim(),
        })),
    };
    setSubmitting(true);
    try {
      const created = await executeCommand(
        commandKey('device.asset.create', payload.hardwareSn, payload),
        (intent) => createPlatformDeviceAsset(payload, intent),
      );
      message.success('设备资产已创建，真实设备联网后会自动验收');
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
          : '机构永久归属已写入，机构安装联网即可使用',
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
          key="create"
          type="primary"
          icon={<AppstoreAddOutlined />}
          onClick={() => {
            assetForm.setFieldsValue({
              expectedPortCount: 1,
              factoryBags: [{ bagCode: '' }],
            });
            setPortCount(1);
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
        message="永久归属 · 自动验收 · 联网即用"
        description={
          mode === 'platform'
            ? '机器验收发生在分配租户之前，只接受真实 MCU、真实摄像头和可信运行证据。'
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
          try {
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
            message.success('已根据最新真实证据重新计算验收结果');
          } catch (error) {
            message.error(errorMessage(error));
            throw error;
          }
        }}
        onChanged={reload}
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
          message="硬件 SN 和厂家初始袋创建后不能替换"
          description="OneNet 设备名固定等于硬件 SN；每个投口必须登记一个真实、唯一的空袋码。"
          style={{ marginBottom: 20 }}
        />
        <Form form={assetForm} layout="vertical">
          <Form.Item
            name="hardwareSn"
            label="硬件 SN / OneNet 设备名"
            rules={[{ required: true }, {
              pattern: /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/,
              message: '请输入 1～64 位合法硬件 SN',
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
                onChange={(value) => setPortCount(Number(value) || 1)}
              />
            </Form.Item>
          </Space>
          {Array.from({ length: portCount }, (_, index) => (
            <Form.Item
              key={index}
              name={['factoryBags', index, 'bagCode']}
              label={`${index + 1} 号投口厂家初始袋码`}
              rules={[
                { required: true },
                {
                  pattern: /^[A-Za-z0-9_-]{8,64}$/,
                  message: '请输入 8～64 位袋码',
                },
              ]}
            >
              <Input maxLength={64} />
            </Form.Item>
          ))}
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
