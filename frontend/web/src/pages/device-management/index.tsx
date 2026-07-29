import { useEffect, useRef, useState } from 'react';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  Empty,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import {
  listDeviceDeployments,
  type DeviceConfigurationApplicationStatus,
  type DeviceDeployment,
  type DeviceDeploymentLifecycleStatus,
} from '@/api/deviceDirectory';
import { ApiProblem } from '@/api/request';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';

const lifecycleLabels: Record<DeviceDeploymentLifecycleStatus, string> = {
  PENDING_INSTALL: '待安装',
  COMMISSIONING: '调试中',
  ENABLED: '已启用',
  MAINTENANCE: '维护中',
  DISABLED: '已停用',
  ENDED: '已结束',
};

const lifecycleColors: Record<DeviceDeploymentLifecycleStatus, string> = {
  PENDING_INSTALL: 'default',
  COMMISSIONING: 'processing',
  ENABLED: 'success',
  MAINTENANCE: 'warning',
  DISABLED: 'default',
  ENDED: 'default',
};

const configurationLabels: Record<
  DeviceConfigurationApplicationStatus,
  string
> = {
  PENDING: '待下发',
  EDGE_SAVED: '边缘已保存',
  APPLIED: '已应用',
  FAILED: '应用失败',
};

const configurationColors: Record<
  DeviceConfigurationApplicationStatus,
  string
> = {
  PENDING: 'processing',
  EDGE_SAVED: 'cyan',
  APPLIED: 'success',
  FAILED: 'error',
};

const edgeConnectionLabels: Record<string, string> = {
  ONLINE: '在线',
  OFFLINE: '离线',
  UNKNOWN: '未知',
};

function edgeConnectionColor(status: string | null): string {
  if (status === 'ONLINE') return 'success';
  if (status === 'OFFLINE') return 'error';
  return 'default';
}

function requestErrorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '设备部署加载失败';
}

export default function DeviceManagementPage() {
  const directoryScope = useDirectoryScope();
  const organizationScope = useOrganizationScope(directoryScope);
  const actionRef = useRef<ActionType>(null);
  const [tableError, setTableError] = useState<string>();

  useEffect(() => {
    setTableError(undefined);
    actionRef.current?.reload();
  }, [directoryScope.context, organizationScope.organizationCode]);

  const columns: ProColumns<DeviceDeployment>[] = [
    {
      title: '部署',
      dataIndex: 'deploymentCode',
      search: false,
      width: 220,
      render: (_, deployment) => (
        <div>
          <Typography.Text strong copyable>
            {deployment.deploymentCode}
          </Typography.Text>
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
      fieldProps: {
        placeholder: '输入硬件序列号',
      },
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
      valueEnum: {
        PENDING_INSTALL: { text: lifecycleLabels.PENDING_INSTALL },
        COMMISSIONING: { text: lifecycleLabels.COMMISSIONING },
        ENABLED: { text: lifecycleLabels.ENABLED },
        MAINTENANCE: { text: lifecycleLabels.MAINTENANCE },
        DISABLED: { text: lifecycleLabels.DISABLED },
        ENDED: { text: lifecycleLabels.ENDED },
      },
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
      title: '投口数',
      dataIndex: 'portCount',
      search: false,
      align: 'right',
      width: 100,
    },
    {
      title: '边缘连接',
      dataIndex: 'edgeConnectionStatus',
      valueType: 'select',
      valueEnum: {
        ONLINE: { text: '在线' },
        OFFLINE: { text: '离线' },
        UNKNOWN: { text: '未知' },
      },
      render: (_, deployment) => (
        <Tag color={edgeConnectionColor(deployment.edgeConnectionStatus)}>
          {deployment.edgeConnectionStatus
            ? edgeConnectionLabels[deployment.edgeConnectionStatus]
              ?? deployment.edgeConnectionStatus
            : '未上报'}
        </Tag>
      ),
    },
    {
      title: '配置摘要',
      dataIndex: 'configurationApplicationStatus',
      valueType: 'select',
      width: 190,
      valueEnum: {
        PENDING: { text: configurationLabels.PENDING },
        EDGE_SAVED: { text: configurationLabels.EDGE_SAVED },
        APPLIED: { text: configurationLabels.APPLIED },
        FAILED: { text: configurationLabels.FAILED },
      },
      render: (_, deployment) => (
        <Space direction="vertical" size={2}>
          {deployment.configurationApplicationStatus ? (
            <Tag
              color={
                configurationColors[
                  deployment.configurationApplicationStatus
                ]
              }
            >
              {
                configurationLabels[
                  deployment.configurationApplicationStatus
                ]
              }
            </Tag>
          ) : (
            <Tag>尚无配置</Tag>
          )}
          <Typography.Text type="secondary">
            最新{' '}
            {deployment.latestConfigurationVersion === null
              ? '—'
              : `v${deployment.latestConfigurationVersion}`}
            {' / '}已应用{' '}
            {deployment.appliedConfigurationVersion === null
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
  ];

  const content = (() => {
    if (directoryScope.loading || organizationScope.loading) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在加载设备目录" />
        </div>
      );
    }
    if (!directoryScope.context) {
      return <Empty description="请选择目标租户" />;
    }
    if (!organizationScope.organizationOptions.length) {
      return <Empty description="当前租户尚无机构" />;
    }
    if (!organizationScope.organizationCode) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在应用机构范围" />
        </div>
      );
    }
    return (
      <>
        {tableError && (
          <Alert
            showIcon
            type="error"
            message="设备部署加载失败"
            description={tableError}
            style={{ marginBottom: 16 }}
          />
        )}
        <ProTable<DeviceDeployment>
          {...proTableConfig}
          actionRef={actionRef}
          rowKey="deploymentCode"
          columns={columns}
          scroll={{ x: 1280 }}
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
              />
            </Space>
          )}
          request={async (params) => {
            if (
              !directoryScope.context
              || !organizationScope.organizationCode
            ) {
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
                  businessEnabled:
                    params.businessEnabled === undefined
                      ? undefined
                      : String(params.businessEnabled) === 'true',
                  hardwareSn:
                    typeof params.hardwareSn === 'string'
                      ? params.hardwareSn.trim() || undefined
                      : undefined,
                  edgeConnectionStatus:
                    params.edgeConnectionStatus as string | undefined,
                  configurationApplicationStatus:
                    params.configurationApplicationStatus as
                      | DeviceConfigurationApplicationStatus
                      | undefined,
                },
              );
              return {
                data: page.items,
                total: page.total,
                success: true,
              };
            } catch (error) {
              setTableError(requestErrorMessage(error));
              return { data: [], total: 0, success: false };
            }
          }}
        />
      </>
    );
  })();

  return (
    <PageContainer
      {...pageHeader(
        '设备管理',
        '按机构查看设备部署、边缘连接与配置应用状态。',
      )}
    >
      <DirectoryScopeBar scope={directoryScope} />
      {content}
    </PageContainer>
  );
}
