import { useEffect, useRef, useState } from 'react';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  DeploymentUnitOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import {
  createOrganizationDeviceDeploymentFromTenantPool,
  createPlatformDeviceAsset,
  listDeviceDeployments,
  listPlatformDeviceAssetAllocations,
  listPlatformDeviceAssets,
  listTenantDeviceAssetAllocations,
  type CreateDeviceAssetRequest,
  type DeviceAsset,
  type DeviceAssetLifecycleStatus,
  type DeviceConfigurationApplicationStatus,
  type DeviceDeployment,
  type DeviceDeploymentLifecycleStatus,
  type DeviceTenantAllocation,
  type DeviceTenantAllocationStatus,
} from '@/api/deviceDirectory';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { useAuthStore } from '@/stores/authStore';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import DeviceAccessDrawer from './DeviceAccessDrawer';
import DeviceAssetDrawer from './DeviceAssetDrawer';
import {
  allocationColors,
  allocationLabels,
  assetColors,
  assetLabels,
  configurationColors,
  configurationLabels,
  connectionStatusColor,
  connectionStatusLabel,
  lifecycleColors,
  lifecycleLabels,
} from './devicePresentation';

interface AssetFormValues {
  hardwareSn: string;
  modelCode: string;
  productionBatch?: string;
  expectedPortCount: number;
}

interface DeploymentFormValues {
  organizationCode: string;
}

interface SelectedDeployment {
  organizationCode: string;
  deploymentCode: string;
}

type DeviceTab = 'assets' | 'allocations' | 'deployments' | 'pool';

function requestErrorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '设备数据加载失败';
}

export default function DeviceManagementPage() {
  const directoryScope = useDirectoryScope();
  const organizationScope = useOrganizationScope(directoryScope);
  const accountType = useAuthStore((state) => state.session?.accountType);
  const hasCapability = useAuthStore((state) => state.hasCapability);
  const executeCommand = useCommandExecutor();
  const platform = directoryScope.platform;
  const tenantPrincipal = accountType === 'TENANT_PRINCIPAL';
  const canManageTenantPool = !platform
    && (tenantPrincipal || hasCapability('device.allocation.manage'));
  const deploymentActionRef = useRef<ActionType>(null);
  const assetActionRef = useRef<ActionType>(null);
  const allocationActionRef = useRef<ActionType>(null);
  const [assetForm] = Form.useForm<AssetFormValues>();
  const [deploymentForm] = Form.useForm<DeploymentFormValues>();
  const [activeTab, setActiveTab] = useState<DeviceTab>(
    platform ? 'assets' : 'deployments',
  );
  const [tableError, setTableError] = useState<string>();
  const [assetError, setAssetError] = useState<string>();
  const [allocationError, setAllocationError] = useState<string>();
  const [selectedDeployment, setSelectedDeployment] =
    useState<SelectedDeployment>();
  const [selectedHardwareSn, setSelectedHardwareSn] = useState<string>();
  const [assetModalOpen, setAssetModalOpen] = useState(false);
  const [assetSubmitting, setAssetSubmitting] = useState(false);
  const [deployingAllocation, setDeployingAllocation] =
    useState<DeviceTenantAllocation>();
  const [deploymentSubmitting, setDeploymentSubmitting] = useState(false);

  useEffect(() => {
    setTableError(undefined);
    setSelectedDeployment(undefined);
    deploymentActionRef.current?.reload();
  }, [directoryScope.context, organizationScope.organizationCode]);

  useEffect(() => {
    if (platform && !['assets', 'allocations', 'deployments'].includes(activeTab)) {
      setActiveTab('assets');
    }
    if (!platform && !canManageTenantPool && activeTab !== 'deployments') {
      setActiveTab('deployments');
    }
  }, [activeTab, canManageTenantPool, platform]);

  const openDeployment = (deployment: DeviceDeployment) => {
    setSelectedDeployment({
      organizationCode: deployment.organizationCode,
      deploymentCode: deployment.deploymentCode,
    });
  };

  const deploymentColumns: ProColumns<DeviceDeployment>[] = [
    {
      title: '部署',
      dataIndex: 'deploymentCode',
      search: false,
      width: 220,
      render: (_, deployment) => (
        <div>
          <Typography.Link
            strong
            copyable
            onClick={() => openDeployment(deployment)}
          >
            {deployment.deploymentCode}
          </Typography.Link>
          <br />
          <Typography.Text type="secondary">
            {deployment.organizationCode}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '硬件',
      dataIndex: 'hardwareSn',
      width: 220,
      fieldProps: { placeholder: '输入硬件序列号' },
      render: (_, deployment) => (
        <div>
          <Typography.Text copyable>
            {deployment.asset.hardwareSn}
          </Typography.Text>
          <br />
          <Typography.Text type="secondary">
            {deployment.asset.modelCode}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '部署状态',
      dataIndex: 'lifecycleStatus',
      valueType: 'select',
      valueEnum: Object.fromEntries(
        Object.entries(lifecycleLabels).map(([key, text]) => [key, { text }]),
      ),
      render: (_, deployment) => (
        <Tag color={lifecycleColors[deployment.lifecycleStatus]}>
          {lifecycleLabels[deployment.lifecycleStatus]}
        </Tag>
      ),
    },
    {
      title: '经营开关',
      dataIndex: 'businessEnabled',
      valueType: 'select',
      valueEnum: {
        true: { text: '已开启' },
        false: { text: '已关闭' },
      },
      render: (_, deployment) => (
        <Tag color={deployment.businessEnabled ? 'success' : 'default'}>
          {deployment.businessEnabled ? '已开启' : '已关闭'}
        </Tag>
      ),
    },
    {
      title: 'OneNet 传输',
      dataIndex: 'oneNetConnectionStatus',
      valueType: 'select',
      width: 140,
      valueEnum: {
        ONLINE: { text: '在线' },
        OFFLINE: { text: '离线' },
        UNKNOWN: { text: '未知' },
      },
      render: (_, deployment) => (
        <Tag color={connectionStatusColor(deployment.oneNetConnectionStatus)}>
          {connectionStatusLabel(deployment.oneNetConnectionStatus)}
        </Tag>
      ),
    },
    {
      title: '业务有效在线',
      dataIndex: 'edgeConnectionStatus',
      valueType: 'select',
      width: 140,
      valueEnum: {
        ONLINE: { text: '在线' },
        OFFLINE: { text: '离线' },
        UNKNOWN: { text: '未知' },
      },
      render: (_, deployment) => (
        <Tag color={connectionStatusColor(deployment.edgeConnectionStatus)}>
          {connectionStatusLabel(deployment.edgeConnectionStatus)}
        </Tag>
      ),
    },
    {
      title: '配置应用',
      dataIndex: 'configurationApplicationStatus',
      valueType: 'select',
      width: 190,
      valueEnum: Object.fromEntries(
        Object.entries(configurationLabels).map(([key, text]) => [key, { text }]),
      ),
      render: (_, deployment) => (
        <Space direction="vertical" size={2}>
          {deployment.configurationApplicationStatus ? (
            <Tag color={configurationColors[deployment.configurationApplicationStatus]}>
              {configurationLabels[deployment.configurationApplicationStatus]}
            </Tag>
          ) : <Tag>尚无配置</Tag>}
          <Typography.Text type="secondary">
            最新 {deployment.latestConfigurationVersion === null
              ? '—'
              : `v${deployment.latestConfigurationVersion}`}
            {' / '}已应用 {deployment.appliedConfigurationVersion === null
              ? '—'
              : `v${deployment.appliedConfigurationVersion}`}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updatedAt',
      search: false,
      width: 190,
      render: (_, deployment) => formatShanghaiTime(deployment.updatedAt),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 120,
      fixed: 'right',
      render: (_, deployment) => (
        <Button
          type="link"
          icon={<SafetyCertificateOutlined />}
          onClick={() => openDeployment(deployment)}
        >
          接入管理
        </Button>
      ),
    },
  ];

  const assetColumns: ProColumns<DeviceAsset>[] = [
    {
      title: '硬件序列号',
      dataIndex: 'hardwareSn',
      width: 210,
      fieldProps: { placeholder: '输入硬件序列号' },
      render: (_, asset) => (
        <Typography.Link
          strong
          copyable
          onClick={() => setSelectedHardwareSn(asset.hardwareSn)}
        >
          {asset.hardwareSn}
        </Typography.Link>
      ),
    },
    { title: '型号', dataIndex: 'modelCode', width: 160 },
    {
      title: '生产批次',
      dataIndex: 'productionBatch',
      width: 150,
      render: (_, asset) => asset.productionBatch || '—',
    },
    {
      title: '资产状态',
      dataIndex: 'lifecycleStatus',
      valueType: 'select',
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
      title: '当前部署',
      dataIndex: 'currentDeployment',
      search: false,
      render: (_, asset) => asset.currentDeployment ? (
        <div>
          <Typography.Text copyable>
            {asset.currentDeployment.deploymentCode}
          </Typography.Text>
          <br />
          <Typography.Text type="secondary">
            {asset.currentDeployment.tenantCode}
            {' / '}
            {asset.currentDeployment.organizationCode}
          </Typography.Text>
        </div>
      ) : <Typography.Text type="secondary">尚未部署</Typography.Text>,
    },
    {
      title: 'OneNet 映射',
      dataIndex: 'oneNetMapping',
      search: false,
      render: (_, asset) => asset.oneNetMapping ? (
        <Space direction="vertical" size={0}>
          <Typography.Text>{asset.oneNetMapping.productId}</Typography.Text>
          <Typography.Text type="secondary" copyable>
            {asset.oneNetMapping.deviceName}
          </Typography.Text>
        </Space>
      ) : '—',
    },
    {
      title: '登记时间',
      dataIndex: 'createdAt',
      search: false,
      width: 180,
      render: (_, asset) => formatShanghaiTime(asset.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      fixed: 'right',
      width: 90,
      render: (_, asset) => (
        <Button type="link" onClick={() => setSelectedHardwareSn(asset.hardwareSn)}>
          详情
        </Button>
      ),
    },
  ];

  const allocationColumns: ProColumns<DeviceTenantAllocation>[] = [
    ...(platform ? [{
      title: '租户',
      dataIndex: 'tenantCode',
      width: 160,
      fieldProps: { placeholder: '输入租户编码' },
    } satisfies ProColumns<DeviceTenantAllocation>] : []),
    {
      title: '硬件',
      dataIndex: 'hardwareSn',
      width: 220,
      render: (_, allocation) => (
        <Space direction="vertical" size={0}>
          {platform ? (
            <Typography.Link
              copyable
              onClick={() => setSelectedHardwareSn(allocation.hardwareSn)}
            >
              {allocation.hardwareSn}
            </Typography.Link>
          ) : (
            <Typography.Text copyable>{allocation.hardwareSn}</Typography.Text>
          )}
          <Typography.Text type="secondary">{allocation.modelCode}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '分配状态',
      dataIndex: 'status',
      valueType: 'select',
      valueEnum: Object.fromEntries(
        Object.entries(allocationLabels).map(([key, text]) => [key, { text }]),
      ),
      render: (_, allocation) => (
        <Tag color={allocationColors[allocation.allocationStatus]}>
          {allocationLabels[allocation.allocationStatus]}
        </Tag>
      ),
    },
    {
      title: '所在位置',
      dataIndex: 'currentOrganizationCode',
      search: false,
      render: (_, allocation) => allocation.currentDeploymentCode ? (
        <Space direction="vertical" size={0}>
          <Typography.Text copyable>
            {allocation.currentDeploymentCode}
          </Typography.Text>
          <Typography.Text type="secondary">
            {allocation.currentOrganizationCode}
          </Typography.Text>
        </Space>
      ) : allocation.allocationStatus === 'ACTIVE' ? (
        <Tag color="processing">租户池中，尚未部署</Tag>
      ) : '—',
    },
    {
      title: 'Device Key',
      dataIndex: 'credentialRotationRequired',
      search: false,
      width: 130,
      render: (_, allocation) => allocation.credentialRotationRequired
        ? <Tag color="warning">待轮换</Tag>
        : <Tag>无待办</Tag>,
    },
    {
      title: '分配时间',
      dataIndex: 'allocatedAt',
      search: false,
      width: 180,
      render: (_, allocation) => formatShanghaiTime(allocation.allocatedAt),
    },
    {
      title: '结束事实',
      dataIndex: 'endedAt',
      search: false,
      render: (_, allocation) => allocation.endedAt ? (
        <Space direction="vertical" size={0}>
          <span>{formatShanghaiTime(allocation.endedAt)}</span>
          <Typography.Text type="secondary">
            {allocation.endMode || '—'}
            {allocation.endReason ? ` · ${allocation.endReason}` : ''}
          </Typography.Text>
        </Space>
      ) : '—',
    },
    ...(canManageTenantPool ? [{
      title: '操作',
      valueType: 'option' as const,
      width: 130,
      fixed: 'right' as const,
      render: (_: unknown, allocation: DeviceTenantAllocation) => {
        const deployable =
          allocation.allocationStatus === 'ACTIVE'
          && !allocation.currentDeploymentCode
          && allocation.assetLifecycleStatus === 'ALLOCATED'
          && !allocation.credentialRotationRequired;
        return deployable ? (
          <Button
            type="link"
            icon={<DeploymentUnitOutlined />}
            onClick={() => {
              deploymentForm.setFieldsValue({
                organizationCode: organizationScope.organizationCode,
              });
              setDeployingAllocation(allocation);
            }}
          >
            部署到机构
          </Button>
        ) : <Typography.Text type="secondary">不可部署</Typography.Text>;
      },
    } satisfies ProColumns<DeviceTenantAllocation>] : []),
  ];

  const deploymentTable = (
    <ProTable<DeviceDeployment>
      {...proTableConfig}
      actionRef={deploymentActionRef}
      rowKey="deploymentCode"
      columns={deploymentColumns}
      scroll={{ x: 1560 }}
      headerTitle={(
        <Space>
          <Typography.Text strong>目标机构</Typography.Text>
          <Select
            aria-label="目标机构"
            showSearch
            optionFilterProp="label"
            style={{ width: 360 }}
            value={organizationScope.organizationCode}
            options={organizationScope.organizationOptions}
            onChange={organizationScope.setOrganizationCode}
            placeholder="选择机构"
          />
        </Space>
      )}
      request={async (params) => {
        if (!directoryScope.context || !organizationScope.organizationCode) {
          return { data: [], total: 0, success: true };
        }
        try {
          setTableError(undefined);
          const page = await listDeviceDeployments(
            directoryScope.context,
            organizationScope.organizationCode,
            {
              page: params.current,
              pageSize: params.pageSize,
              lifecycleStatus: params.lifecycleStatus as
                | DeviceDeploymentLifecycleStatus
                | undefined,
              businessEnabled: params.businessEnabled === undefined
                ? undefined
                : String(params.businessEnabled) === 'true',
              hardwareSn: typeof params.hardwareSn === 'string'
                ? params.hardwareSn.trim() || undefined
                : undefined,
              edgeConnectionStatus:
                params.edgeConnectionStatus as string | undefined,
              oneNetConnectionStatus:
                params.oneNetConnectionStatus as string | undefined,
              configurationApplicationStatus:
                params.configurationApplicationStatus as
                  | DeviceConfigurationApplicationStatus
                  | undefined,
            },
          );
          return { data: page.items, total: page.total, success: true };
        } catch (error) {
          setTableError(requestErrorMessage(error));
          return { data: [], total: 0, success: false };
        }
      }}
    />
  );

  const assetTable = (
    <ProTable<DeviceAsset>
      {...proTableConfig}
      actionRef={assetActionRef}
      rowKey="hardwareSn"
      columns={assetColumns}
      scroll={{ x: 1300 }}
      headerTitle="平台物理设备资产"
      toolBarRender={() => [
        <Button
          key="register"
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            assetForm.resetFields();
            assetForm.setFieldValue('expectedPortCount', 1);
            setAssetModalOpen(true);
          }}
        >
          登记硬件
        </Button>,
      ]}
      request={async (params) => {
        try {
          setAssetError(undefined);
          const page = await listPlatformDeviceAssets({
            page: params.current,
            pageSize: params.pageSize,
            hardwareSn: typeof params.hardwareSn === 'string'
              ? params.hardwareSn.trim() || undefined
              : undefined,
            modelCode: typeof params.modelCode === 'string'
              ? params.modelCode.trim() || undefined
              : undefined,
            productionBatch: typeof params.productionBatch === 'string'
              ? params.productionBatch.trim() || undefined
              : undefined,
            lifecycleStatus: params.lifecycleStatus as
              | DeviceAssetLifecycleStatus
              | undefined,
          });
          return { data: page.items, total: page.total, success: true };
        } catch (error) {
          setAssetError(requestErrorMessage(error));
          return { data: [], total: 0, success: false };
        }
      }}
    />
  );

  const allocationTable = (
    <ProTable<DeviceTenantAllocation>
      {...proTableConfig}
      actionRef={allocationActionRef}
      rowKey="allocationUid"
      columns={allocationColumns}
      scroll={{ x: 1320 }}
      headerTitle={platform ? '租户分配历史' : '租户设备池与分配历史'}
      request={async (params) => {
        try {
          setAllocationError(undefined);
          const query = {
            page: params.current,
            pageSize: params.pageSize,
            status: params.status as DeviceTenantAllocationStatus | undefined,
            hardwareSn: typeof params.hardwareSn === 'string'
              ? params.hardwareSn.trim() || undefined
              : undefined,
          };
          const page = platform
            ? await listPlatformDeviceAssetAllocations({
                ...query,
                tenantCode: typeof params.tenantCode === 'string'
                  ? params.tenantCode.trim() || undefined
                  : undefined,
              })
            : await listTenantDeviceAssetAllocations(query);
          return { data: page.items, total: page.total, success: true };
        } catch (error) {
          setAllocationError(requestErrorMessage(error));
          return { data: [], total: 0, success: false };
        }
      }}
    />
  );

  const submitAsset = async () => {
    const values = await assetForm.validateFields();
    const payload: CreateDeviceAssetRequest = {
      hardwareSn: values.hardwareSn.trim(),
      modelCode: values.modelCode.trim(),
      productionBatch: values.productionBatch?.trim() || null,
      expectedPortCount: values.expectedPortCount,
    };
    setAssetSubmitting(true);
    try {
      const created = await executeCommand(
        commandKey('device.asset.create', payload.hardwareSn, payload),
        (intent) => createPlatformDeviceAsset(payload, intent),
      );
      message.success('硬件资产已登记；这不表示 OneNet 设备或密钥已创建');
      setAssetModalOpen(false);
      assetActionRef.current?.reload();
      setSelectedHardwareSn(created.hardwareSn);
    } catch {
      // The request layer presents the traceable problem. Retryable failures
      // keep the same caller-owned command intent.
    } finally {
      setAssetSubmitting(false);
    }
  };

  const submitDeployment = async () => {
    if (!deployingAllocation || !directoryScope.context) return;
    const values = await deploymentForm.validateFields();
    const payload = {
      allocationUid: deployingAllocation.allocationUid,
      expectedAllocationVersion: deployingAllocation.allocationVersion,
    };
    setDeploymentSubmitting(true);
    try {
      const created = await executeCommand(
        commandKey(
          'device.deployment.create-from-pool',
          `${values.organizationCode}:${deployingAllocation.allocationUid}`,
          payload,
        ),
        (intent) => createOrganizationDeviceDeploymentFromTenantPool(
          directoryScope.context!,
          values.organizationCode,
          payload,
          intent,
        ),
      );
      message.success('已创建新机构部署，请重新发布配置并等待技术就绪');
      setDeployingAllocation(undefined);
      organizationScope.setOrganizationCode(values.organizationCode);
      setActiveTab('deployments');
      setSelectedDeployment({
        organizationCode: values.organizationCode,
        deploymentCode: created.deploymentCode,
      });
      allocationActionRef.current?.reload();
      deploymentActionRef.current?.reload();
    } catch (error) {
      if (error instanceof ApiProblem && error.isVersionConflict) {
        message.warning('分配状态已变化，已刷新租户设备池，请重新确认');
        allocationActionRef.current?.reload();
      }
    } finally {
      setDeploymentSubmitting(false);
    }
  };

  const tabError = activeTab === 'assets'
    ? assetError
    : activeTab === 'deployments'
      ? tableError
      : allocationError;

  const content = (() => {
    if (directoryScope.loading || organizationScope.loading) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在加载设备目录" />
        </div>
      );
    }
    if (!platform && !directoryScope.context) {
      return <Empty description="当前会话没有可用租户范围" />;
    }
    const items = platform ? [
      { key: 'assets', label: '平台资产', children: assetTable },
      { key: 'allocations', label: '租户分配', children: allocationTable },
      { key: 'deployments', label: '机构部署', children: deploymentTable },
    ] : canManageTenantPool ? [
      { key: 'deployments', label: '机构部署', children: deploymentTable },
      { key: 'pool', label: '租户设备池', children: allocationTable },
    ] : [
      { key: 'deployments', label: '机构部署', children: deploymentTable },
    ];
    return (
      <>
        {tabError && (
          <Alert
            showIcon
            type="error"
            message="设备数据加载失败"
            description={tabError}
            style={{ marginBottom: 16 }}
          />
        )}
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key as DeviceTab)}
          items={items}
        />
      </>
    );
  })();

  return (
    <PageContainer
      {...pageHeader(
        '设备管理',
        '按平台资产、租户分配和机构部署分层管理，调拨始终经过租户设备池。',
      )}
    >
      {platform && <DirectoryScopeBar scope={directoryScope} />}
      {content}

      <DeviceAccessDrawer
        open={!!selectedDeployment}
        context={directoryScope.context}
        organizationCode={selectedDeployment?.organizationCode}
        deploymentCode={selectedDeployment?.deploymentCode}
        onClose={() => setSelectedDeployment(undefined)}
        onUpdated={() => {
          deploymentActionRef.current?.reload();
          allocationActionRef.current?.reload();
        }}
        onReturnedToPool={() => {
          setSelectedDeployment(undefined);
          setActiveTab('pool');
          deploymentActionRef.current?.reload();
          allocationActionRef.current?.reload();
        }}
      />

      {platform && (
        <DeviceAssetDrawer
          open={!!selectedHardwareSn}
          hardwareSn={selectedHardwareSn}
          tenantOptions={directoryScope.tenantOptions}
          onClose={() => setSelectedHardwareSn(undefined)}
          onUpdated={() => {
            assetActionRef.current?.reload();
            allocationActionRef.current?.reload();
          }}
        />
      )}

      <Modal
        title="登记平台物理设备"
        open={assetModalOpen}
        confirmLoading={assetSubmitting}
        okText="确认登记"
        onOk={() => void submitAsset()}
        onCancel={() => !assetSubmitting && setAssetModalOpen(false)}
        destroyOnClose
      >
        <Alert
          showIcon
          type="info"
          message="这里登记的是 EcoBin 本地库存事实"
          description="网页不创建 OneNet 设备或生成 Device Key。运维人员仍需预先在 OneNet 建立同名设备，并把密钥配置到对应香橙派。"
          style={{ marginBottom: 16 }}
        />
        <Form<AssetFormValues>
          form={assetForm}
          layout="vertical"
          disabled={assetSubmitting}
        >
          <Form.Item
            name="hardwareSn"
            label="硬件序列号"
            extra="全平台唯一、区分大小写，登记后不可修改。"
            rules={[
              { required: true, message: '请输入硬件序列号' },
              { max: 64, message: '最多 64 个字符' },
            ]}
          >
            <Input placeholder="例如 SN-001" />
          </Form.Item>
          <Form.Item
            name="modelCode"
            label="设备型号"
            rules={[
              { required: true, message: '请输入设备型号' },
              { max: 100, message: '最多 100 个字符' },
            ]}
          >
            <Input placeholder="例如 ECOBIN-V1" />
          </Form.Item>
          <Form.Item
            name="productionBatch"
            label="生产批次"
            rules={[{ max: 64, message: '最多 64 个字符' }]}
          >
            <Input placeholder="可选，例如 2026-08" />
          </Form.Item>
          <Form.Item
            name="expectedPortCount"
            label="设备投口数量"
            rules={[
              { required: true, message: '请输入投口数量' },
              { type: 'integer', min: 1, max: 6, message: '投口数量为 1 至 6' },
            ]}
          >
            <InputNumber min={1} max={6} precision={0} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="从租户设备池部署到机构"
        open={!!deployingAllocation}
        confirmLoading={deploymentSubmitting}
        okText="创建新部署"
        onOk={() => void submitDeployment()}
        onCancel={() => !deploymentSubmitting && setDeployingAllocation(undefined)}
        destroyOnClose
      >
        <Alert
          showIcon
          type="info"
          message="这是调拨的第二步"
          description="系统会创建全新部署实例。原机构的已结束部署、订单、清运和统计不会迁移或覆盖。"
          style={{ marginBottom: 16 }}
        />
        {deployingAllocation && (
          <Descriptions size="small" column={1} bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label="租户池硬件">
              {deployingAllocation.hardwareSn} · {deployingAllocation.modelCode}
            </Descriptions.Item>
            <Descriptions.Item label="投口数量">
              {deployingAllocation.expectedPortCount}
            </Descriptions.Item>
          </Descriptions>
        )}
        <Form<DeploymentFormValues>
          form={deploymentForm}
          layout="vertical"
          disabled={deploymentSubmitting}
        >
          <Form.Item
            name="organizationCode"
            label="目标机构"
            rules={[{ required: true, message: '请选择目标机构' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              options={organizationScope.organizationOptions}
              placeholder="选择机构"
            />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
