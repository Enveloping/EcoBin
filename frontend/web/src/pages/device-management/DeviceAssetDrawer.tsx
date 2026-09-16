import { useAuthStore } from '@/stores/authStore';
import HelpTip from '@/components/HelpTip';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Descriptions,
  Divider,
  Drawer,
  Dropdown,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Space,
  Spin,
  Steps,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CloudSyncOutlined,
  DownloadOutlined,
  EditOutlined,
  HistoryOutlined,
  MonitorOutlined,
  MoreOutlined,
  QrcodeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import QRCode from 'qrcode';
import {
  confirmPlatformDeliveryNotStarted,
  getDeviceConfigurationVersion,
  getOrganizationDeviceRuntime,
  getPlatformDeviceConfigurationApplication,
  getPlatformDeviceConfigurationVersion,
  getPlatformDeviceFactoryProgress,
  getPlatformDeliveryRecoveryQuarantine,
  getPlatformDeviceRuntime,
  getTenantDeviceRuntime,
  listDeviceAcceptanceEvidence,
  listDeviceConfigurationVersions,
  listPlatformDeviceConfigurationVersions,
  listPlatformDeviceTechnicalIssues,
  releaseDeviceConfiguration,
  quarantinePlatformDeliveryRecovery,
  terminatePlatformAbnormalDelivery,
  resynchronizePlatformDeviceConfiguration,
  rollForwardPlatformDeviceConfiguration,
  startPlatformBaselineMeasurementAttempt,
  type DeviceAcceptanceEvidence,
  type DeviceAsset,
  type DeviceConfigurationApplication,
  type DeviceConfigurationReleaseRequest,
  type DeviceConfigurationVersion,
  type DeviceConfigurationVersionSummary,
  type DeviceFactoryProgress,
  type DeviceFactoryProgressEvidenceSummary,
  type DevicePortRuntime,
  type DeviceRuntime,
  type DeviceTechnicalIssue,
  type DeliveryRecoveryQuarantine,
} from '@/api/deviceDirectory';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';
import {
  acceptanceColors,
  acceptanceLabels,
  assetColors,
  assetLabels,
  booleanEvidence,
  configurationColors,
  configurationLabels,
  operatorFacingTechnicalText,
  runtimeStatusColor,
  runtimeStatusLabel,
  technicalIssueStateLabel,
} from './devicePresentation';
import {
  buildFactoryProgressSteps,
  collectFactoryProgressCodes,
  factoryActionLabel,
  factoryFailureGuidance,
  factoryIssueAlertType,
  factoryProgressSummary,
  factoryStageLabel,
  sealStatusLabel,
  taskStateLabel,
  technicalResultLabel,
} from './factoryProgressPresentation';
import {
  architectureGenerationLabel,
  businessAdmissionPresentation,
  businessProcessStateLabel,
  compatibilityPresentation,
  deviceGateStateLabel,
  deviceManagementDetail,
  deviceManagementSummary,
  formatProtocolVersion,
  type DeviceManagementDetail,
  type DeviceManagementSummary,
} from './deviceManagementPresentation';
import RemoteSupportPanel from './RemoteSupportPanel';
import { operatorErrorMessage } from './operatorErrorPresentation';

export type DeviceManagementMode = 'platform' | 'tenant' | 'organization';
export type DeviceControlKind = 'disable' | 'restore' | 'retire';

interface DeviceAssetDrawerProps {
  open: boolean;
  mode: DeviceManagementMode;
  asset?: DeviceAsset;
  organizationCode?: string;
  onClose: () => void;
  onAssignTenant: (asset: DeviceAsset) => void;
  onAssignOrganization: (asset: DeviceAsset) => void;
  onControl: (asset: DeviceAsset, kind: DeviceControlKind) => void;
  onReevaluateAcceptance: (asset: DeviceAsset) => Promise<void>;
  onChanged: () => void;
}

interface DailyConfigurationEdits {
  reason: string;
  ports?: Array<{
    displayName?: string;
    enabled?: boolean;
  }>;
}

interface PlatformConfigurationRecovery {
  reason: string;
}

interface ManualBaselineAttempt {
  causeFixedConfirmed: boolean;
  emptyBagConfirmed: boolean;
  reason: string;
}

interface DeliveryNotStartedConfirmationForm {
  causeFixedConfirmed: boolean;
  deliveryNeverStartedConfirmed: boolean;
  reason: string;
}

interface DeliveryRecoveryQuarantineForm {
  physicalOutcomeUnknownConfirmed: boolean;
  causeFixedConfirmed: boolean;
  devicePowerCycledConfirmed: boolean;
  motionAreaClearConfirmed: boolean;
  deliveryDoorClosedConfirmed: boolean;
  mechanismClearConfirmed: boolean;
  reason: string;
}

interface AbnormalDeliveryTerminationForm {
  physicalOutcomeUnknownConfirmed: boolean;
  devicePoweredOffConfirmed: boolean;
  motionAreaClearConfirmed: boolean;
  deliveryDoorClosedConfirmed: boolean;
  mechanismClearConfirmed: boolean;
  reason: string;
}

type PlatformRecoveryKind = 'roll-forward' | 'resynchronize';

type TechnicalIssueLoadStatus = 'idle' | 'loading' | 'loaded' | 'error';

interface TechnicalIssueLoadState {
  status: TechnicalIssueLoadStatus;
  data: DeviceTechnicalIssue[];
  error?: string;
  hasLoaded: boolean;
}

interface RuntimeLoadState {
  status: TechnicalIssueLoadStatus;
  data?: DeviceRuntime;
  error?: string;
  hasLoaded: boolean;
}

interface FactoryProgressLoadState {
  status: TechnicalIssueLoadStatus;
  data?: DeviceFactoryProgress;
  error?: string;
  hasLoaded: boolean;
}

const EMPTY_TECHNICAL_ISSUE_LOAD: TechnicalIssueLoadState = {
  status: 'idle',
  data: [],
  hasLoaded: false,
};

const EMPTY_RUNTIME_LOAD: RuntimeLoadState = {
  status: 'idle',
  hasLoaded: false,
};

const EMPTY_FACTORY_PROGRESS_LOAD: FactoryProgressLoadState = {
  status: 'idle',
  hasLoaded: false,
};

function errorMessage(error: unknown): string {
  return operatorErrorMessage(
    error,
    '设备信息暂时无法加载或操作未完成，请稍后再试',
  );
}

function RuntimeTag({ value }: { value?: string | null }) {
  return (
    <Tag color={runtimeStatusColor(value)}>
      {runtimeStatusLabel(value)}
    </Tag>
  );
}

function optionalTime(value?: string | null): string {
  return value ? formatShanghaiTime(value) : '尚无记录';
}

function portWeightText(port: DevicePortRuntime, includeKind = true): string {
  if (port.weightValueAvailable === false) return '本次没有有效重量';
  if (port.reportedWeightGrams == null) return '尚无重量数据';
  const kind = includeKind && port.weightValueKind
    ? ` · ${runtimeStatusLabel(port.weightValueKind)}`
    : '';
  return `${port.reportedWeightGrams} 克${kind}`;
}

function portAttentionStates(port: DevicePortRuntime) {
  return [
    ['投递门', port.deliveryDoorState],
    ['门驱动', port.deliveryDoorActuatorHealth],
    ['门检测', port.deliveryDoorContactState],
    ['投递门指令', port.lastDeliveryDoorOutputStatus],
    ['清运锁', port.cleanLockPowerState],
    ['电磁阀', port.cleanSolenoidHealth],
    ['清运门', port.cleanDoorRecordedState],
    ['称重', port.weightSensorHealth],
    ['重量测量', port.weightMeasurementStatus],
    ['红外传感器', port.infraredSensorHealth],
    ['烟雾传感器', port.smokeSensorHealth],
    ['烟雾', port.smokeState],
    ['安全', port.safetyStatus],
  ] as const;
}

function portCondition(port: DevicePortRuntime) {
  if (port.configuredEnabled === false) {
    return { color: 'default', label: '已停用' };
  }
  if (port.configuredEnabled == null) {
    return { color: 'default', label: '待确认' };
  }
  const colors = portAttentionStates(port)
    .map(([, value]) => runtimeStatusColor(value));
  if (colors.some(color => color === 'error' || color === 'warning')) {
    return { color: 'error', label: '异常' };
  }
  if (colors.some(color => color === 'default')) {
    return { color: 'default', label: '待确认' };
  }
  if (colors.some(color => color === 'processing')) {
    return { color: 'processing', label: '作业中' };
  }
  return { color: 'success', label: '正常' };
}

function weightFullnessText(port: DevicePortRuntime): string {
  if (port.weightFull == null) return '尚无结果';
  return port.weightFull ? '已满' : '未满';
}

function infraredFullnessText(port: DevicePortRuntime): string {
  if (port.infraredSensorHealth !== 'OK') return '尚无结果';
  if (port.infraredValue === 'BLOCKED') return '已满';
  if (port.infraredValue === 'CLEAR') return '未满';
  return '尚无结果';
}

function totalPortWeightText(runtime?: DeviceRuntime): string {
  if (!runtime) return '尚未读取';
  const enabledPorts = runtime.ports.filter(port => port.configuredEnabled === true);
  if (!enabledPorts.length) return '尚无已启用投口';
  const unavailable = enabledPorts.filter(port => (
    port.weightValueAvailable === false || port.reportedWeightGrams == null
  )).length;
  if (unavailable) return `${unavailable} 个投口暂无有效重量`;
  const grams = enabledPorts.reduce(
    (total, port) => total + (port.reportedWeightGrams ?? 0),
    0,
  );
  return `${grams} 克`;
}

function deviceFullnessText(runtime?: DeviceRuntime): string {
  if (!runtime) return '尚未读取';
  const enabledPorts = runtime.ports.filter(port => port.configuredEnabled === true);
  if (!enabledPorts.length) return '尚无已启用投口';
  if (enabledPorts.some(port => (
    port.weightFull === true || port.infraredValue === 'BLOCKED'
  ))) return '存在满溢投口';
  if (enabledPorts.every(port => (
    port.weightFull === false
      && port.infraredSensorHealth === 'OK'
      && port.infraredValue === 'CLEAR'
  ))) return '未满';
  return '暂时无法确认';
}

function configurationVersionText(runtime?: DeviceRuntime): string {
  if (!runtime?.configuration.latestPublishedVersion) return '尚无配置';
  const published = runtime.configuration.latestPublishedVersion;
  const applied = runtime.configuration.latestAppliedVersion;
  if (runtime.configuration.latestPreciselyApplied) return `V${published} · 已生效`;
  if (applied == null) return `V${published} · 等待设备应用`;
  return `当前 V${applied} · 等待 V${published}`;
}

function wholeDeviceCondition(
  runtime: DeviceRuntime | undefined,
  technicalIssues: DeviceTechnicalIssue[],
  technicalIssuesKnown: boolean,
) {
  if (!runtime) return { color: 'default', label: '暂时无法确认' };
  if (!technicalIssuesKnown) {
    return { color: 'default', label: '正在确认' };
  }
  if (technicalIssues.length > 0) return { color: 'error', label: '异常' };
  const healthColors = [
    runtime.health.mcuLinkStatus,
    runtime.health.safetyStatus,
    runtime.health.aggregateWeightHealth,
    runtime.health.cameraHealth,
    runtime.health.localStorageHealth,
    runtime.health.clockSyncHealth,
    ...runtime.ports.flatMap(port => portAttentionStates(port).map(([, value]) => value)),
  ].map(value => runtimeStatusColor(value));
  if (healthColors.some(color => color === 'error' || color === 'warning')) {
    return { color: 'error', label: '异常' };
  }
  if (healthColors.some(color => color === 'default')) {
    return { color: 'default', label: '暂时无法确认' };
  }
  return { color: 'success', label: '正常' };
}

function PortRuntimePanel({ port }: { port: DevicePortRuntime }) {
  const technicalStates = [
    ['投递门状态', port.deliveryDoorState],
    ['投递门驱动机构', port.deliveryDoorActuatorHealth],
    ['投递门检测', port.deliveryDoorContactState],
    ['最近投递门指令', port.lastDeliveryDoorCommand],
    ['投递门指令结果', port.lastDeliveryDoorOutputStatus],
    ['清运锁供电', port.cleanLockPowerState],
    ['清运电磁阀', port.cleanSolenoidHealth],
    ['清运门记录', port.cleanDoorRecordedState],
    ['清运门状态来源', port.cleanDoorStateBasis],
    ['称重传感器', port.weightSensorHealth],
    ['重量取值方式', port.weightValueKind],
    ['重量测量', port.weightMeasurementStatus],
    ['红外传感器', port.infraredSensorHealth],
    ['红外结果', port.infraredValue],
    ['满溢传感器类型', port.fullnessSensorKind],
    ['满溢传感器结果', port.fullnessSensorValue],
    ['烟雾传感器', port.smokeSensorHealth],
    ['烟雾状态', port.smokeState],
    ['投口安全状态', port.safetyStatus],
  ].filter((entry): entry is [string, string] => typeof entry[1] === 'string');
  return (
    <Space
      className="device-port-runtime-panel"
      direction="vertical"
      size={8}
      style={{ width: '100%' }}
    >
    <Descriptions
      className="device-port-runtime-primary"
      size="small"
      bordered
      column={2}
    >
      <Descriptions.Item label="投递门状态">
        <RuntimeTag value={port.deliveryDoorState} />
      </Descriptions.Item>
      <Descriptions.Item label="投递门驱动机构">
        <RuntimeTag value={port.deliveryDoorActuatorHealth} />
      </Descriptions.Item>
      <Descriptions.Item label="投递门检测">
        <RuntimeTag value={port.deliveryDoorContactState} />
      </Descriptions.Item>
      <Descriptions.Item label="最近投递门命令">
        <Space size={4} wrap>
          <Typography.Text>
            {port.lastDeliveryDoorCommand
              ? runtimeStatusLabel(port.lastDeliveryDoorCommand)
              : '尚无命令'}
          </Typography.Text>
          {port.lastDeliveryDoorOutputStatus && (
            <Tag color={runtimeStatusColor(
              port.lastDeliveryDoorOutputStatus,
            )}>
              {runtimeStatusLabel(port.lastDeliveryDoorOutputStatus)}
            </Tag>
          )}
        </Space>
      </Descriptions.Item>
      <Descriptions.Item label="清运锁供电">
        <RuntimeTag value={port.cleanLockPowerState} />
      </Descriptions.Item>
      <Descriptions.Item label="清运电磁阀">
        <RuntimeTag value={port.cleanSolenoidHealth} />
      </Descriptions.Item>
      <Descriptions.Item label="清运门记录状态">
        <RuntimeTag value={port.cleanDoorRecordedState} />
      </Descriptions.Item>
      <Descriptions.Item label="清运门状态来源">
        {runtimeStatusLabel(port.cleanDoorStateBasis)}
      </Descriptions.Item>
      <Descriptions.Item label="清运员关门确认">
        {port.cleanerPhysicalCloseConfirmed == null
          ? '本次状态记录未提供'
          : port.cleanerPhysicalCloseConfirmed
            ? <Tag color="success">已现场确认关闭</Tag>
            : <Tag color="warning">尚未确认关闭</Tag>}
      </Descriptions.Item>
      <Descriptions.Item label="称重传感器">
        <RuntimeTag value={port.weightSensorHealth} />
      </Descriptions.Item>
      <Descriptions.Item label="最近重量">
        {portWeightText(port)}
      </Descriptions.Item>
      <Descriptions.Item label="重量测量状态">
        <RuntimeTag value={port.weightMeasurementStatus} />
      </Descriptions.Item>
      <Descriptions.Item label="红外传感器">
        <Space size={4} wrap>
          <RuntimeTag value={port.infraredSensorHealth} />
          <RuntimeTag value={port.infraredValue} />
        </Space>
      </Descriptions.Item>
      <Descriptions.Item label="满溢传感器">
        {port.fullnessSensorKind ? (
          <Space size={4} wrap>
            <Typography.Text>
              {runtimeStatusLabel(port.fullnessSensorKind)}
            </Typography.Text>
            <RuntimeTag value={port.fullnessSensorValue} />
            {port.representativeDistanceMm != null && (
              <Typography.Text type="secondary">
                {port.representativeDistanceMm} mm
              </Typography.Text>
            )}
          </Space>
        ) : '尚无数据'}
      </Descriptions.Item>
      <Descriptions.Item label="烟雾传感器">
        <Space size={4} wrap>
          <RuntimeTag value={port.smokeSensorHealth} />
          <RuntimeTag value={port.smokeState} />
        </Space>
      </Descriptions.Item>
      <Descriptions.Item label="投口安全状态">
        <RuntimeTag value={port.safetyStatus} />
      </Descriptions.Item>
      <Descriptions.Item label="配置状态">
        {port.configuredEnabled == null
          ? '尚无配置'
          : port.configuredEnabled
            ? <Tag color="success">已启用</Tag>
            : <Tag>未启用</Tag>}
      </Descriptions.Item>
      <Descriptions.Item label="最后观测时间" span={2}>
        {optionalTime(port.lastObservedAt)}
      </Descriptions.Item>
    </Descriptions>
    <Collapse
      ghost
      size="small"
      items={[{
        key: `port-${port.portNo}-technical-states`,
        label: '报修信息（报修时使用）',
        children: (
          <Descriptions size="small" column={2}>
            {technicalStates.map(([label, value]) => (
              <Descriptions.Item key={label} label={`${label}代码`}>
                <Typography.Text copyable code>{value}</Typography.Text>
              </Descriptions.Item>
            ))}
          </Descriptions>
        ),
      }]}
    />
    </Space>
  );
}

function RuntimeTechnicalDetails({
  runtime,
}: {
  runtime: DeviceRuntime;
}) {
  const { health } = runtime;
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Typography.Title level={5} style={{ margin: 0 }}>
        运行诊断
      </Typography.Title>
      <Descriptions size="small" bordered column={2}>
        <Descriptions.Item label="设备联网">
          <RuntimeTag value={health.oneNetConnectionStatus} />
        </Descriptions.Item>
        <Descriptions.Item label="设备状态发生时间">
          {optionalTime(health.oneNetStatusObservedAt)}
        </Descriptions.Item>
        <Descriptions.Item label="平台收到状态时间">
          {optionalTime(health.oneNetStatusReceivedAt)}
        </Descriptions.Item>
        <Descriptions.Item label="设备运行状态">
          <RuntimeTag value={health.edgeConnectionStatus} />
        </Descriptions.Item>
        <Descriptions.Item label="平台收到运行状态">
          {optionalTime(health.trustedRuntimeReceivedAt)}
        </Descriptions.Item>
        <Descriptions.Item label="设备控制板通信">
          <RuntimeTag value={health.mcuLinkStatus} />
        </Descriptions.Item>
        <Descriptions.Item label="整机安全状态">
          <RuntimeTag value={health.safetyStatus} />
        </Descriptions.Item>
        <Descriptions.Item label="整机称重健康">
          <RuntimeTag value={health.aggregateWeightHealth} />
        </Descriptions.Item>
        <Descriptions.Item label="摄像头健康">
          <RuntimeTag value={health.cameraHealth} />
        </Descriptions.Item>
        <Descriptions.Item label="本地存储">
          <RuntimeTag value={health.localStorageHealth} />
        </Descriptions.Item>
        <Descriptions.Item label="设备时钟">
          <RuntimeTag value={health.clockSyncHealth} />
        </Descriptions.Item>
        <Descriptions.Item label="当前作业占用">
          {runtime.occupied ? (
            <Space size={4} wrap>
              <RuntimeTag value={runtime.occupancyKind} />
              <Typography.Text type="secondary">
                {optionalTime(runtime.occupiedAt)}
              </Typography.Text>
            </Space>
          ) : <Tag color="success">空闲</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="控制板连接状态">
          <RuntimeTag value={health.uartState} />
        </Descriptions.Item>
        <Descriptions.Item label="最近心跳记录">
          {optionalTime(health.lastHeartbeatAt)}
        </Descriptions.Item>
        <Descriptions.Item label="最近设备事件">
          {optionalTime(health.lastDeviceEventAt)}
        </Descriptions.Item>
        <Descriptions.Item label="等待上传的设备记录">
          {health.pendingReliableEventCount ?? '尚无数据'}
        </Descriptions.Item>
        <Descriptions.Item label="设备上报的配置版本" span={2}>
          {health.orangePiReportedConfigurationVersion == null
            ? '尚无数据'
            : `v${health.orangePiReportedConfigurationVersion}`}
        </Descriptions.Item>
      </Descriptions>
      <Collapse
        ghost
        size="small"
        items={[{
          key: 'runtime-raw-status-codes',
          label: '原始运行状态代码（报修时使用）',
          children: (
            <Descriptions size="small" bordered column={2}>
              <Descriptions.Item label="设备联网状态代码">
                <Typography.Text copyable code>
                  {health.oneNetConnectionStatus}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="设备运行状态代码">
                <Typography.Text copyable code>
                  {health.edgeConnectionStatus}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="控制板通信状态代码">
                <Typography.Text copyable code>
                  {health.mcuLinkStatus}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="整机安全状态代码">
                <Typography.Text copyable code>
                  {health.safetyStatus}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="称重状态代码">
                <Typography.Text copyable code>
                  {health.aggregateWeightHealth}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="摄像头状态代码">
                <Typography.Text copyable code>
                  {health.cameraHealth}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="存储状态代码">
                <Typography.Text copyable code>
                  {health.localStorageHealth}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="设备时钟状态代码">
                <Typography.Text copyable code>
                  {health.clockSyncHealth}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="控制板连接状态代码">
                <Typography.Text copyable code>
                  {health.uartState}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="当前作业类型代码">
                <Typography.Text copyable code>
                  {runtime.occupancyKind ?? 'NONE'}
                </Typography.Text>
              </Descriptions.Item>
            </Descriptions>
          ),
        }]}
      />
    </Space>
  );
}

function RuntimePortsPanel({ runtime }: { runtime: DeviceRuntime }) {
  return runtime.ports.length ? (
    <Table<DevicePortRuntime>
      size="small"
      rowKey="portNo"
      pagination={false}
      dataSource={runtime.ports}
      columns={[
        {
          title: '投口',
          key: 'port',
          render: (_, port) => (
            <Space direction="vertical" size={0}>
              <Typography.Text strong>{port.displayName}</Typography.Text>
              <Typography.Text type="secondary">{port.portNo} 号</Typography.Text>
            </Space>
          ),
        },
        {
          title: '当前重量',
          key: 'weight',
          render: (_, port) => portWeightText(port, false),
        },
        {
          title: '重量满溢',
          key: 'weightFull',
          render: (_, port) => weightFullnessText(port),
        },
        {
          title: '红外满溢',
          key: 'infraredFull',
          render: (_, port) => infraredFullnessText(port),
        },
        {
          title: '投口状态',
          key: 'condition',
          render: (_, port) => {
            const condition = portCondition(port);
            return <Tag color={condition.color}>{condition.label}</Tag>;
          },
        },
        {
          title: '最近数据',
          key: 'observedAt',
          render: (_, port) => optionalTime(port.lastObservedAt),
        },
      ]}
      expandable={{
        expandRowByClick: true,
        expandedRowRender: port => <PortRuntimePanel port={port} />,
        rowExpandable: () => true,
      }}
    />
  ) : (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description="永久分配机构后才会建立投口运行状态"
    />
  );
}
function DeviceManagementAttention({
  management,
}: {
  management: DeviceManagementDetail | DeviceManagementSummary | null;
}) {
  const admission = businessAdmissionPresentation(management);
  const detail = management && 'reasons' in management ? management : null;
  const reasons = detail?.reasons.length
    ? detail.reasons
    : management?.primaryReason
      ? [management.primaryReason]
      : [];
  const attentionRequired = admission.color !== 'success' || reasons.length > 0;
  if (!attentionRequired) return null;
  const visibleReasons = reasons.length ? reasons : [{
    code: 'MANAGEMENT_REASON_NOT_AVAILABLE',
    title: '设备当前状态需要处理',
    description: admission.description,
    blocksNewBusiness: management?.businessAdmission !== 'ACCEPTING',
  }];
  const alertType = admission.color === 'error'
    ? 'error'
    : admission.color === 'warning'
      ? 'warning'
      : 'info';
  return (
    <Alert
      showIcon
      type={alertType}
      message={admission.label}
      description={(
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          {visibleReasons.map((reason, index) => (
            <div key={`${reason.title}-${index}`}>
              <Typography.Text strong>{reason.title}</Typography.Text>
              <br />
              <Typography.Text type="secondary">
                {reason.description}
              </Typography.Text>
            </div>
          ))}
        </Space>
      )}
    />
  );
}

function DeviceManagementTechnicalDetails({
  management,
}: {
  management: DeviceManagementDetail | DeviceManagementSummary | null;
}) {
  if (!management) return <Empty description="尚无软件与通信记录" />;
  const compatibility = compatibilityPresentation(management);
  const detail = 'reasons' in management ? management : null;
  const managed = management.architectureGeneration === 'PERMANENT_V1';
  const protocolItems = detail ? [
    ['云端管理通信版本', detail.managementTransportProtocol],
    ['设备维护通信版本', detail.deviceMaintenanceProtocol],
    ['通信程序与业务程序通信版本', detail.agentBusinessProtocol],
    ['通信程序与更新程序通信版本', detail.agentUpdaterProtocol],
    ['更新程序与业务程序通信版本', detail.updaterBusinessProtocol],
    ['业务程序与控制板通信版本', detail.uartProtocol],
  ] as const : [];
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Typography.Title level={5} style={{ margin: 0 }}>
        软件与通信
      </Typography.Title>
      <Descriptions size="small" bordered column={2}>
        <Descriptions.Item label="设备管理方式">
          {architectureGenerationLabel(management)}
        </Descriptions.Item>
        <Descriptions.Item label="软件配合情况">
          <Tag color={compatibility.color}>{compatibility.label}</Tag>
        </Descriptions.Item>
        {managed && detail && (
          <>
            <Descriptions.Item label="设备作业入口">
              {deviceGateStateLabel(detail.deviceGateState)}
            </Descriptions.Item>
            <Descriptions.Item label="业务程序运行情况">
              {businessProcessStateLabel(
                detail.businessProcessState,
                detail.businessReady,
              )}
            </Descriptions.Item>
            <Descriptions.Item label="业务程序版本">
              {detail.businessVersionName ?? '尚无数据'}
            </Descriptions.Item>
            <Descriptions.Item label="设备通信程序版本">
              {detail.communicationAgentVersion ?? '尚无数据'}
            </Descriptions.Item>
            <Descriptions.Item label="设备更新程序版本">
              {detail.deviceUpdaterVersion ?? '尚无数据'}
            </Descriptions.Item>
            <Descriptions.Item label="控制板程序版本">
              {detail.mcuFirmwareVersion ?? '尚无数据'}
            </Descriptions.Item>
          </>
        )}
        <Descriptions.Item label="管理状态记录时间" span={2}>
          {managed ? optionalTime(management.observedAt) : '旧设备不需要此记录'}
        </Descriptions.Item>
      </Descriptions>
      {managed && protocolItems.some(([, value]) => value != null) && (
        <Descriptions size="small" bordered column={1} title="通信版本">
          {protocolItems.map(([label, value]) => (
            <Descriptions.Item key={label} label={label}>
              {formatProtocolVersion(value)}
            </Descriptions.Item>
          ))}
        </Descriptions>
      )}
    </Space>
  );
}
function mergeConfiguration(
  current: DeviceConfigurationVersion,
  edits: DailyConfigurationEdits,
): DeviceConfigurationReleaseRequest {
  return {
    expectedLatestVersion: current.versionNo,
    reason: edits.reason,
    device: { ...current.device },
    ports: current.ports.map((port, index) => ({
      ...port,
      displayName: edits.ports?.[index]?.displayName ?? port.displayName,
      enabled: edits.ports?.[index]?.enabled ?? port.enabled,
    })),
  };
}

function AcceptanceEvidenceFacts({
  evidence,
}: {
  evidence: DeviceAcceptanceEvidence;
}) {
  const functionalFacts = [
    ['云端连接', evidence.oneNetOnline],
    ['本地存储', evidence.persistentStoreHealthy],
    ['配置安全保存', evidence.configurationPersistenceHealthy],
    ['设备控制板通信', evidence.mcuCommunicationHealthy],
    ['传感器数据', evidence.sensorsHealthy],
    ['摄像头采集', evidence.camerasCaptureHealthy],
    ['测试图片上传', evidence.cameraUploadHealthy],
  ] as const;
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {evidence.failureReasons.length > 0 && (
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          {evidence.failureReasons.map((code) => (
            <Typography.Text key={code} type="secondary">
              {factoryFailureGuidance(code).title}
            </Typography.Text>
          ))}
        </Space>
      )}
      <Descriptions size="small" column={2} bordered>
        {functionalFacts.map(([label, value]) => (
          <Descriptions.Item key={label} label={label}>
            <Tag color={value ? 'success' : 'error'}>
              {booleanEvidence(value)}
            </Tag>
          </Descriptions.Item>
        ))}
        <Descriptions.Item label="设备时间状态（仅供排查）">
          <Tag>
            {evidence.trustedTimeHealthy ? '已同步' : '未同步，不影响验收结论'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="设备控制板来源（诊断）">
          <Tag>{evidence.mcuSimulated ? '模拟来源' : '真实来源'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="摄像头来源（诊断）">
          <Tag>{evidence.camerasSimulated ? '模拟来源' : '真实来源'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="设备入口地址保存">
          {evidence.deviceEntryUrlStored == null ? (
            <Tag>历史记录未采集</Tag>
          ) : (
            <Tag color={evidence.deviceEntryUrlStored ? 'success' : 'error'}>
              {evidence.deviceEntryUrlStored ? '香橙派已可靠保存' : '未可靠保存'}
            </Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="二维码显示状态" span={2}>
          {evidence.deviceEntryUrlMcuApplied == null ? (
            <Tag>历史记录未采集</Tag>
          ) : evidence.deviceEntryUrlMcuApplied ? (
            <Tag color="success">
              完整显示指令已写入串口屏发送队列（按设备规则视为已显示）
            </Tag>
          ) : (
            <Tag color="error">完整显示指令尚未写入发送队列</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="设备检查时间">
          {formatShanghaiTime(evidence.observedAt)}
        </Descriptions.Item>
        <Descriptions.Item label="平台收到时间">
          {formatShanghaiTime(evidence.receivedAt)}
        </Descriptions.Item>
      </Descriptions>
      <Collapse
        ghost
        size="small"
        items={[{
          key: `evidence-${evidence.evidenceUid}-diagnostics`,
          label: '技术诊断（报修时使用）',
          children: (
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="检查记录编号">
                <Typography.Text copyable code>
                  {evidence.evidenceUid}
                </Typography.Text>
              </Descriptions.Item>
              {evidence.failureReasons.length > 0 && (
                <Descriptions.Item label="未通过原因代码">
                  <Space wrap>
                    {evidence.failureReasons.map((code) => (
                      <Typography.Text key={code} copyable code>
                        {code}
                      </Typography.Text>
                    ))}
                  </Space>
                </Descriptions.Item>
              )}
              {evidence.deviceEntryUrlSha256 && (
                <Descriptions.Item label="香橙派保存的入口摘要">
                  <Typography.Text copyable code>
                    {evidence.deviceEntryUrlSha256}
                  </Typography.Text>
                </Descriptions.Item>
              )}
              {evidence.deviceEntryUrlAppliedSha256 && (
                <Descriptions.Item label="控制板写入队列的入口摘要">
                  <Typography.Text copyable code>
                    {evidence.deviceEntryUrlAppliedSha256}
                  </Typography.Text>
                </Descriptions.Item>
              )}
              {evidence.deviceEntryUrlAppliedMcuBootId != null && (
                <Descriptions.Item label="执行写入的控制板启动编号">
                  <Typography.Text copyable code>
                    {evidence.deviceEntryUrlAppliedMcuBootId}
                  </Typography.Text>
                </Descriptions.Item>
              )}
              {evidence.deviceEntryUrlDisplayBasis && (
                <Descriptions.Item label="二维码显示判定依据">
                  <Typography.Text code>
                    {evidence.deviceEntryUrlDisplayBasis}
                  </Typography.Text>
                </Descriptions.Item>
              )}
            </Descriptions>
          ),
        }]}
      />
    </Space>
  );
}

function EvidencePanel({ rows }: { rows: DeviceAcceptanceEvidence[] }) {
  if (!rows.length) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="本次验收尚未保存设备检查记录"
      />
    );
  }
  return (
    <Collapse
      size="small"
      items={rows.map((evidence, index) => ({
        key: evidence.evidenceUid,
        label: (
          <Space wrap>
            <Typography.Text strong>验收记录 {index + 1}</Typography.Text>
            <Tag color={acceptanceColors[evidence.evaluationStatus]}>
              当次判定 {acceptanceLabels[evidence.evaluationStatus]}
            </Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              收到于 {formatShanghaiTime(evidence.receivedAt)}
            </Typography.Text>
          </Space>
        ),
        children: <AcceptanceEvidenceFacts evidence={evidence} />,
      }))}
    />
  );
}

function FactoryEvidenceReference({
  title,
  evidence,
  emptyText,
}: {
  title: string;
  evidence: DeviceFactoryProgressEvidenceSummary | null;
  emptyText: string;
}) {
  return (
    <Card size="small" title={title} style={{ flex: '1 1 280px' }}>
      {!evidence ? (
        <Typography.Text type="secondary">{emptyText}</Typography.Text>
      ) : (
        <>
          <Descriptions size="small" column={1} colon={false}>
            <Descriptions.Item label="当次判定">
              <Tag color={acceptanceColors[evidence.evaluationStatus]}>
                {acceptanceLabels[evidence.evaluationStatus]}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="平台收到">
              {formatShanghaiTime(evidence.receivedAt)}
            </Descriptions.Item>
          </Descriptions>
          <Collapse
            ghost
            size="small"
            items={[{
              key: 'evidence-technical-reference',
              label: '查看技术记录（报修时使用）',
              children: (
                <Descriptions size="small" column={1} colon={false}>
                  <Descriptions.Item label="检查记录编号">
                    <Typography.Text copyable code>{evidence.evidenceUid}</Typography.Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="记录校验值">
                    <Typography.Text
                      copyable={{ text: evidence.evidenceSha256 }}
                      code
                      style={{ overflowWrap: 'anywhere' }}
                    >
                      {evidence.evidenceSha256}
                    </Typography.Text>
                  </Descriptions.Item>
                </Descriptions>
              ),
            }]}
          />
        </>
      )}
    </Card>
  );
}

function FactoryTaskDiagnostics({
  title,
  task,
}: {
  title: string;
  task: DeviceFactoryProgress['acceptanceRequest'];
}) {
  return (
    <Descriptions size="small" column={2} bordered>
      <Descriptions.Item label={`${title}状态`}>
        <Tag color={task.taskState === 'BLOCKED'
          || task.taskState === 'CANCELLED' ? 'error' : 'default'}>
          {taskStateLabel(task.taskState)}
        </Tag>
      </Descriptions.Item>
      <Descriptions.Item label="系统任务编号">
        {task.taskUid
          ? <Typography.Text copyable code>{task.taskUid}</Typography.Text>
          : '尚未创建'}
      </Descriptions.Item>
      {task.latestAttempt && (
        <>
          <Descriptions.Item label="最近尝试">
            {task.latestAttempt.attemptNo == null
              ? '尝试次数未记录'
              : `第 ${task.latestAttempt.attemptNo} 次`}
            {task.latestAttempt.technicalResult
              ? ` · ${technicalResultLabel(task.latestAttempt.technicalResult)}`
              : ''}
          </Descriptions.Item>
          <Descriptions.Item label="物联网平台请求编号">
            {task.latestAttempt.externalRequestId
              ? (
                <Typography.Text copyable>
                  {task.latestAttempt.externalRequestId}
                </Typography.Text>
              )
              : '未记录'}
          </Descriptions.Item>
          <Descriptions.Item label="物联网平台返回信息">
            {task.latestAttempt.httpStatus ?? '-'} / {task.latestAttempt.externalErrorCode ?? '-'}
          </Descriptions.Item>
          <Descriptions.Item label="平台记录时间">
            {optionalTime(task.latestAttempt.recordedAt)}
          </Descriptions.Item>
        </>
      )}
      {(task.blockedDiagnostic || task.latestAttempt?.diagnostic) && (
        <Descriptions.Item label="技术诊断信息" span={2}>
          <Typography.Text code style={{ overflowWrap: 'anywhere' }}>
            {task.blockedDiagnostic ?? task.latestAttempt?.diagnostic}
          </Typography.Text>
        </Descriptions.Item>
      )}
    </Descriptions>
  );
}

function factoryProgressCompleted(progress?: DeviceFactoryProgress): boolean {
  return Boolean(progress
    && progress.status === 'COMPLETED'
    && progress.seal.status === 'SEALED'
    && progress.acceptance.status === 'PASSED'
    && progress.nextActionCodes.length === 0
    && collectFactoryProgressCodes(progress).length === 0);
}

function FactoryProgressPanel({
  retired,
  load,
  onRefresh,
}: {
  retired: boolean;
  load: FactoryProgressLoadState;
  onRefresh: () => void;
}) {
  const progress = load.data;
  const loading = load.status === 'loading';
  if (!progress) {
    return (
      <section aria-labelledby="factory-progress-title">
        <Card
          title={<span id="factory-progress-title">接入与封存进度</span>}
          extra={(
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={loading}
              onClick={onRefresh}
            >
              刷新
            </Button>
          )}
        >
          <Spin spinning={loading}>
            {load.status === 'error' ? (
              <Alert
                type="error"
                showIcon
                message="接入进度加载失败"
                description={load.error}
                action={<Button size="small" onClick={onRefresh}>重试</Button>}
              />
            ) : (
              <div aria-label="正在加载接入与封存进度" style={{ minHeight: 88 }} />
            )}
          </Spin>
        </Card>
      </section>
    );
  }

  const steps = buildFactoryProgressSteps(progress);
  const summary = factoryProgressSummary(progress);
  const issueCodes = collectFactoryProgressCodes(progress);
  const evidenceChanged = progress.acceptance.authoritativeEvidence
    && progress.acceptance.latestEvidence
    && progress.acceptance.authoritativeEvidence.evidenceUid
    !== progress.acceptance.latestEvidence.evidenceUid;

  const completed = factoryProgressCompleted(progress);
  return (
    <section aria-label="接入与封存">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {load.status === 'error' && <Alert type="warning" showIcon message="接入进度刷新失败" description={load.error} />}
        {!retired && !completed && <Alert type={summary.type} showIcon message={summary.message} description={summary.description} />}
        {!retired && !completed && progress.nextActionCodes.length > 0 && <Space wrap>下一步{progress.nextActionCodes.map(action => <Tag key={action}>{factoryActionLabel(action)}</Tag>)}</Space>}
        {!retired && issueCodes.map(code => <Alert key={code} type={factoryIssueAlertType(progress)} showIcon message={factoryFailureGuidance(code).title} description={factoryFailureGuidance(code).action} />)}
        <Collapse size="small" items={[{
          key: 'factory-progress-details', label: retired ? '接入与封存记录' : completed ? '接入与封存 · 已完成' : '接入与封存明细', children: (
            <Card
              title={(
                <Space wrap>
                  <SafetyCertificateOutlined />
                  <Typography.Text strong id="factory-progress-title">
                    接入与封存进度
                  </Typography.Text>
                  <Tag>{factoryStageLabel(progress.currentStage)}</Tag>
                </Space>
              )}
              extra={(
                <Space size={8}>
                  {!retired && progress.seal.status !== 'SEALED' && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      前台每 5 秒刷新
                    </Typography.Text>
                  )}
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    loading={loading}
                    onClick={onRefresh}
                  >
                    刷新
                  </Button>
                </Space>
              )}
              styles={{ body: { padding: 16 } }}
            >
              {load.status === 'error' && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message="进度刷新失败，以下为上一次成功结果"
                  description={load.error}
                />
              )}
              <div
                role="group"
                aria-label="设备出厂接入节点链"
                style={{ overflowX: 'auto', padding: '4px 2px 12px' }}
              >
                <div style={{ minWidth: 650 }}>
                  <Steps
                    size="small"
                    responsive={false}
                    items={steps.map((step) => ({
                      title: step.title,
                      description: step.description,
                      status: step.status,
                    }))}
                  />
                </div>
              </div>
              <Descriptions size="small" column={2} bordered style={{ marginTop: 12 }}>
                <Descriptions.Item label="初始袋码">
                  {progress.factoryBags.verifiedCount}/{progress.factoryBags.expectedPortCount}
                  {progress.factoryBags.complete
                    ? <Tag color="success" style={{ marginLeft: 8 }}>已完整</Tag>
                    : <Tag color="warning" style={{ marginLeft: 8 }}>待补齐</Tag>}
                </Descriptions.Item>
                <Descriptions.Item label="袋码更新次数">
                  {progress.factoryBags.revision}
                </Descriptions.Item>
                <Descriptions.Item label="当前验收结果">
                  <Tag color={acceptanceColors[progress.acceptance.status]}>
                    {acceptanceLabels[progress.acceptance.status]}
                  </Tag>
                  <Typography.Text type="secondary">
                    第 {progress.acceptance.generation} 次验收
                  </Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="封存状态">
                  <Tag color={progress.seal.status === 'SEALED'
                    ? 'success'
                    : progress.seal.status === 'CANCELLED'
                      ? 'error'
                      : progress.seal.status === 'ACKNOWLEDGED'
                        ? 'warning'
                        : 'processing'}>
                    {sealStatusLabel(progress.seal.status)}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="最近判定">
                  {optionalTime(progress.acceptance.lastEvaluatedAt)}
                </Descriptions.Item>
                <Descriptions.Item label="验收通过时间">
                  {optionalTime(progress.acceptance.acceptedAt)}
                </Descriptions.Item>
                <Descriptions.Item label="数据读取时间" span={2}>
                  {formatShanghaiTime(progress.fetchedAt)}
                </Descriptions.Item>
                {!retired && <Descriptions.Item label="下一步" span={2}>
                  {progress.nextActionCodes.length ? (
                    <Space wrap>
                      {progress.nextActionCodes.map((action) => (
                        <Tag key={action}>{factoryActionLabel(action)}</Tag>
                      ))}
                    </Space>
                  ) : '等待系统继续推进'}
                </Descriptions.Item>}
              </Descriptions>

              {evidenceChanged && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginTop: 12 }}
                  message="最近收到的检查记录不是本次验收采用的记录"
                  description="请分别核对下方两份记录；后到的记录不会自动改变已确认的验收结果或封存授权。"
                />
              )}
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 12,
                  width: '100%',
                  marginTop: 12,
                }}
              >
                <FactoryEvidenceReference
                  title="当前验收采用的检查记录"
                  evidence={progress.acceptance.authoritativeEvidence}
                  emptyText="本次验收尚未采用任何设备检查记录"
                />
                <FactoryEvidenceReference
                  title="最近收到的检查记录"
                  evidence={progress.acceptance.latestEvidence}
                  emptyText="管理平台尚未收到设备检查记录"
                />
              </div>

              <Collapse
                size="small"
                style={{ marginTop: 12 }}
                items={[{
                  key: 'factory-task-diagnostics',
                  label: '技术诊断（报修时使用）',
                  children: (
                    <Space direction="vertical" size={12} style={{ width: '100%' }}>
                      {issueCodes.length > 0 && (
                        <Descriptions size="small" column={1} bordered>
                          <Descriptions.Item label="报修代码">
                            <Space wrap>
                              {issueCodes.map((code) => (
                                <Typography.Text key={code} copyable code>
                                  {code}
                                </Typography.Text>
                              ))}
                            </Space>
                          </Descriptions.Item>
                        </Descriptions>
                      )}
                      <FactoryTaskDiagnostics
                        title="设备功能检查指令"
                        task={progress.acceptanceRequest}
                      />
                      <FactoryTaskDiagnostics
                        title="封存授权"
                        task={progress.seal}
                      />
                      <Descriptions size="small" column={2} bordered>
                        <Descriptions.Item label="对应第几次验收">
                          {progress.seal.status === 'NOT_ISSUED'
                            ? '尚未签发'
                            : progress.seal.generation}
                        </Descriptions.Item>
                        <Descriptions.Item label="设备接受授权">
                          {optionalTime(progress.seal.acknowledgedAt)}
                        </Descriptions.Item>
                        <Descriptions.Item label="设备封存时间">
                          {optionalTime(progress.seal.sealedAt)}
                        </Descriptions.Item>
                        <Descriptions.Item label="平台收到完成通知">
                          {optionalTime(progress.seal.completionReceivedAt)}
                        </Descriptions.Item>
                        <Descriptions.Item label="设备清理完成" span={2}>
                          {optionalTime(progress.seal.cleanupCompletedAt)}
                        </Descriptions.Item>
                      </Descriptions>
                    </Space>
                  ),
                }]}
              />
            </Card>
          )
        }]} />
      </Space>
    </section>
  );
}

export default function DeviceAssetDrawer({
  open,
  mode,
  asset,
  onClose,
  onAssignTenant,
  onAssignOrganization,
  onControl,
  onReevaluateAcceptance,
  onChanged,
}: DeviceAssetDrawerProps) {
  // A tenant-wide list can contain devices from several organizations.
  const organizationCode = asset?.organizationCode ?? undefined;
  const canPublishConfiguration = useAuthStore((state) => state.hasCapability('device.configuration.manage'));
  const executeCommand = useCommandExecutor();
  const [configForm] = Form.useForm<DailyConfigurationEdits>();
  const [recoveryForm] = Form.useForm<PlatformConfigurationRecovery>();
  const [baselineForm] = Form.useForm<ManualBaselineAttempt>();
  const [deliveryRecoveryForm] =
    Form.useForm<DeliveryNotStartedConfirmationForm>();
  const [deliveryQuarantineForm] =
    Form.useForm<DeliveryRecoveryQuarantineForm>();
  const [abnormalTerminationForm] =
    Form.useForm<AbnormalDeliveryTerminationForm>();
  const [evidence, setEvidence] = useState<DeviceAcceptanceEvidence[]>([]);
  const [evidenceExpanded, setEvidenceExpanded] = useState(false);
  const [evidenceLoaded, setEvidenceLoaded] = useState(false);
  const [runtimeLoad, setRuntimeLoad] =
    useState<RuntimeLoadState>(EMPTY_RUNTIME_LOAD);
  const [factoryProgressLoad, setFactoryProgressLoad] =
    useState<FactoryProgressLoadState>(EMPTY_FACTORY_PROGRESS_LOAD);
  const [technicalIssueLoad, setTechnicalIssueLoad] =
    useState<TechnicalIssueLoadState>(EMPTY_TECHNICAL_ISSUE_LOAD);
  const [versions, setVersions] = useState<DeviceConfigurationVersionSummary[]>([]);
  const [latestVersion, setLatestVersion] = useState<DeviceConfigurationVersion>();
  const [latestApplication, setLatestApplication] =
    useState<DeviceConfigurationApplication>();
  const [loadingEvidence, setLoadingEvidence] = useState(false);
  const runtimeRequest = useRef(0);
  const factoryProgressRequest = useRef(0);
  const factoryProgressInFlight = useRef<number | null>(null);
  const factoryProgressRefreshQueued = useRef(false);
  const factoryProgressSealed = useRef(false);
  const technicalIssueRequest = useRef(0);
  const [loadingConfiguration, setLoadingConfiguration] = useState(false);
  const [configurationExpanded, setConfigurationExpanded] = useState(false);
  const [remoteSupportOpen, setRemoteSupportOpen] = useState(false);
  const [configurationError, setConfigurationError] = useState<string>();
  const configurationRequest = useRef(0);
  const [configurationModalOpen, setConfigurationModalOpen] = useState(false);
  const [recoveryKind, setRecoveryKind] = useState<PlatformRecoveryKind>();
  const [submitting, setSubmitting] = useState(false);
  const [recoverySubmitting, setRecoverySubmitting] = useState(false);
  const [baselineSubmitting, setBaselineSubmitting] = useState(false);
  const [baselineIssue, setBaselineIssue] = useState<DeviceTechnicalIssue>();
  const [deliveryRecoverySubmitting, setDeliveryRecoverySubmitting] =
    useState(false);
  const [deliveryRecoveryIssue, setDeliveryRecoveryIssue] =
    useState<DeviceTechnicalIssue>();
  const [deliveryQuarantineSubmitting, setDeliveryQuarantineSubmitting] =
    useState(false);
  const [deliveryQuarantineIssue, setDeliveryQuarantineIssue] =
    useState<DeviceTechnicalIssue>();
  const [abnormalTerminationSubmitting,
    setAbnormalTerminationSubmitting] = useState(false);
  const [abnormalTerminationIssue, setAbnormalTerminationIssue] =
    useState<DeviceTechnicalIssue>();
  const [deliveryQuarantineEvidence, setDeliveryQuarantineEvidence] =
    useState<DeliveryRecoveryQuarantine>();
  const [deliveryQuarantineEvidenceIssue,
    setDeliveryQuarantineEvidenceIssue] = useState<DeviceTechnicalIssue>();
  const [deliveryQuarantineEvidenceLoading,
    setDeliveryQuarantineEvidenceLoading] = useState(false);
  const [reevaluating, setReevaluating] = useState(false);
  const [entryQrDataUrl, setEntryQrDataUrl] = useState<string>();
  const [entryQrError, setEntryQrError] = useState(false);
  const [entryQrOpen, setEntryQrOpen] = useState(false);
  const technicalIssues = technicalIssueLoad.data;
  const loadingTechnicalIssues = technicalIssueLoad.status === 'loading';
  const loadingRuntime = runtimeLoad.status === 'loading';
  const hardwareSn = asset?.hardwareSn;
  const softwareManagement = runtimeLoad.data
    ? deviceManagementDetail(runtimeLoad.data) ?? deviceManagementSummary(asset)
    : deviceManagementSummary(asset);
  const deviceCondition = wholeDeviceCondition(
    runtimeLoad.data,
    technicalIssues,
    mode !== 'platform' || technicalIssueLoad.hasLoaded,
  );

  const canConfigure = Boolean(asset && organizationCode);

  const loadFactoryProgress = useCallback(async (
    background = false,
  ) => {
    if (!hardwareSn || mode !== 'platform') return;
    if (factoryProgressInFlight.current != null) {
      if (!background) factoryProgressRefreshQueued.current = true;
      return;
    }
    const requestId = ++factoryProgressRequest.current;
    factoryProgressInFlight.current = requestId;
    setFactoryProgressLoad((current) => ({
      ...current,
      status: background && current.hasLoaded ? current.status : 'loading',
      error: undefined,
    }));
    try {
      do {
        factoryProgressRefreshQueued.current = false;
        const data = await getPlatformDeviceFactoryProgress(hardwareSn);
        if (factoryProgressRequest.current !== requestId) return;
        factoryProgressSealed.current = data.seal.status === 'SEALED';
        setFactoryProgressLoad({
          status: 'loaded',
          data,
          hasLoaded: true,
        });
      } while (factoryProgressRefreshQueued.current);
    } catch (error) {
      if (factoryProgressRequest.current !== requestId) return;
      setFactoryProgressLoad((current) => ({
        ...current,
        status: 'error',
        error: errorMessage(error),
      }));
    } finally {
      if (factoryProgressInFlight.current === requestId) {
        factoryProgressInFlight.current = null;
      }
    }
  }, [hardwareSn, mode]);

  const loadEvidence = async () => {
    if (!asset || mode !== 'platform') return;
    setLoadingEvidence(true);
    try {
      setEvidence(await listDeviceAcceptanceEvidence(asset.hardwareSn));
      setEvidenceLoaded(true);
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setLoadingEvidence(false);
    }
  };

  const loadRuntime = async () => {
    if (!asset) return;
    if (mode === 'organization' && !organizationCode) return;
    const requestId = ++runtimeRequest.current;
    setRuntimeLoad((current) => ({
      ...current,
      status: 'loading',
      error: undefined,
    }));
    try {
      const data = mode === 'platform'
        ? await getPlatformDeviceRuntime(asset.hardwareSn)
        : mode === 'tenant'
          ? await getTenantDeviceRuntime(asset.hardwareSn)
          : await getOrganizationDeviceRuntime(
            organizationCode!,
            asset.deviceCode,
          );
      if (runtimeRequest.current !== requestId) return;
      setRuntimeLoad({
        status: 'loaded',
        data,
        hasLoaded: true,
      });
    } catch (error) {
      if (runtimeRequest.current !== requestId) return;
      setRuntimeLoad((current) => ({
        ...current,
        status: 'error',
        error: errorMessage(error),
      }));
    }
  };

  const loadTechnicalIssues = async () => {
    if (!asset || mode !== 'platform') return;
    const requestId = ++technicalIssueRequest.current;
    setTechnicalIssueLoad((current) => ({
      ...current,
      status: 'loading',
      error: undefined,
    }));
    try {
      const data = await listPlatformDeviceTechnicalIssues(asset.hardwareSn);
      if (technicalIssueRequest.current !== requestId) return;
      setTechnicalIssueLoad({
        status: 'loaded',
        data,
        hasLoaded: true,
      });
    } catch (error) {
      if (technicalIssueRequest.current !== requestId) return;
      const readableError = errorMessage(error);
      setTechnicalIssueLoad((current) => ({
        ...current,
        status: 'error',
        error: readableError,
      }));
      message.error(readableError);
    }
  };

  const loadConfiguration = async () => {
    if (!asset || !canConfigure) return;
    const requestId = ++configurationRequest.current;
    setConfigurationError(undefined);
    setLoadingConfiguration(true);
    try {
      const page = mode === 'platform'
        ? await listPlatformDeviceConfigurationVersions(
          asset.hardwareSn,
          { limit: 20 },
        )
        : await listDeviceConfigurationVersions(
          organizationCode!,
          asset.deviceCode,
          { limit: 20 },
        );
      if (configurationRequest.current !== requestId) return;
      setVersions(page.items);
      if (page.items.length) {
        const latest = page.items[0];
        const version = mode === 'platform'
          ? await getPlatformDeviceConfigurationVersion(
            asset.hardwareSn,
            latest.versionNo,
          )
          : await getDeviceConfigurationVersion(
            organizationCode!,
            asset.deviceCode,
            latest.versionNo,
          );
        if (configurationRequest.current !== requestId) return;
        setLatestVersion(version);
        if (mode === 'platform') {
          const application = await getPlatformDeviceConfigurationApplication(
            asset.hardwareSn,
            latest.application.applicationUid,
          );
          if (configurationRequest.current !== requestId) return;
          setLatestApplication(application);
        } else {
          setLatestApplication(undefined);
        }
      } else {
        setLatestVersion(undefined);
        setLatestApplication(undefined);
      }
    } catch (error) {
      if (configurationRequest.current !== requestId) return;
      setConfigurationError(errorMessage(error));
      message.error(errorMessage(error));
    } finally {
      if (configurationRequest.current === requestId) setLoadingConfiguration(false);
    }
  };

  useEffect(() => {
    runtimeRequest.current += 1;
    factoryProgressRequest.current += 1;
    factoryProgressInFlight.current = null;
    factoryProgressRefreshQueued.current = false;
    factoryProgressSealed.current = false;
    technicalIssueRequest.current += 1;
    configurationRequest.current += 1;
    setEntryQrOpen(false);
    setRemoteSupportOpen(false);
    if (!open) return;
    setConfigurationExpanded(false);
    setConfigurationError(undefined);
    setEvidence([]);
    setEvidenceExpanded(false);
    setEvidenceLoaded(false);
    setRuntimeLoad(EMPTY_RUNTIME_LOAD);
    setFactoryProgressLoad(EMPTY_FACTORY_PROGRESS_LOAD);
    setTechnicalIssueLoad(EMPTY_TECHNICAL_ISSUE_LOAD);
    setVersions([]);
    setLatestVersion(undefined);
    setLatestApplication(undefined);
    setBaselineIssue(undefined);
    setDeliveryRecoveryIssue(undefined);
    setDeliveryQuarantineIssue(undefined);
    setAbnormalTerminationIssue(undefined);
    setDeliveryQuarantineEvidence(undefined);
    setDeliveryQuarantineEvidenceIssue(undefined);
    void loadTechnicalIssues();
    void loadConfiguration();
    // The stable identities below intentionally define a new drawer target.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    open,
    mode,
    asset?.hardwareSn,
    asset?.deviceCode,
    asset?.organizationCode,
    organizationCode,
  ]);

  useEffect(() => {
    if (!open || mode !== 'platform' || !hardwareSn) return () => undefined;
    let stopped = false;
    let timer: number | undefined;

    const schedule = () => {
      if (
        stopped
        || factoryProgressSealed.current
        || asset?.lifecycleStatus === 'RETIRED'
        || document.visibilityState !== 'visible'
      ) return;
      timer = window.setTimeout(() => {
        timer = undefined;
        void poll();
      }, 5_000);
    };
    const poll = async () => {
      if (stopped || document.visibilityState !== 'visible') return;
      await loadFactoryProgress(true);
      schedule();
    };
    const visibilityChanged = () => {
      if (document.visibilityState !== 'visible') {
        if (timer != null) window.clearTimeout(timer);
        timer = undefined;
        return;
      }
      if (timer == null && !factoryProgressSealed.current && asset?.lifecycleStatus !== 'RETIRED') void poll();
    };

    void poll();
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => {
      stopped = true;
      if (timer != null) window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', visibilityChanged);
      factoryProgressRequest.current += 1;
      factoryProgressInFlight.current = null;
      factoryProgressRefreshQueued.current = false;
    };
  }, [asset?.lifecycleStatus, hardwareSn, loadFactoryProgress, mode, open]);

  useEffect(() => {
    if (!open || !asset) return () => undefined;
    void loadRuntime();
    const interval = window.setInterval(() => {
      void loadRuntime();
    }, 15_000);
    return () => {
      window.clearInterval(interval);
      runtimeRequest.current += 1;
    };
    // A new target must start a separate polling lifecycle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    open,
    mode,
    asset?.hardwareSn,
    asset?.deviceCode,
    organizationCode,
  ]);

  useEffect(() => {
    let cancelled = false;
    setEntryQrDataUrl(undefined);
    setEntryQrError(false);
    if (!open || !entryQrOpen || !asset?.deviceEntryUrl) return () => undefined;
    void QRCode.toDataURL(asset.deviceEntryUrl, {
      width: 360,
      margin: 2,
      errorCorrectionLevel: 'M',
      color: { dark: '#142318', light: '#ffffff' },
    })
      .then((dataUrl) => {
        if (!cancelled) setEntryQrDataUrl(dataUrl);
      })
      .catch(() => {
        if (!cancelled) setEntryQrError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [open, entryQrOpen, asset?.deviceEntryUrl]);

  const downloadEntryQr = () => {
    if (!asset || !entryQrDataUrl) return;
    const safeName = asset.hardwareSn.replace(/[^0-9A-Za-z_-]/g, '_');
    const link = document.createElement('a');
    link.href = entryQrDataUrl;
    link.download = `ecobin-device-${safeName}.png`;
    link.click();
  };

  const actionButtons = useMemo(() => {
    if (!asset) return null;
    if (mode === 'platform') {
      return (
        <Space wrap>
          {!asset.tenantCode && asset.lifecycleStatus === 'NORMAL' && (
            <Button type="primary" onClick={() => onAssignTenant(asset)}>
              永久分配租户
            </Button>
          )}
          {asset.lifecycleStatus === 'DISABLED' && (
            <Button type="primary" onClick={() => onControl(asset, 'restore')}>恢复</Button>
          )}
          <Dropdown trigger={['click']} menu={{
            items: [
              {
                key: 'reevaluate', label: '重新核对设备检查结果', disabled: reevaluating || asset.lifecycleStatus !== 'NORMAL', onClick: async () => {
                  setReevaluating(true);
                  try {
                    await onReevaluateAcceptance(asset);
                    await Promise.all([loadEvidence(), loadTechnicalIssues(), loadFactoryProgress()]);
                  } catch (error) { message.error(errorMessage(error)); }
                  finally { setReevaluating(false); }
                }
              },
              ...(asset.lifecycleStatus === 'NORMAL' ? [{ key: 'disable', label: '禁用设备', onClick: () => onControl(asset, 'disable') }] : []),
              ...(asset.lifecycleStatus !== 'RETIRED' ? [{ key: 'retire', label: '报废设备', danger: true, onClick: () => onControl(asset, 'retire') }] : []),
            ]
          }}>
            <Button icon={<MoreOutlined />} loading={reevaluating}>更多操作</Button>
          </Dropdown>
        </Space>
      );
    }
    if (mode === 'tenant' && !asset.organizationCode && asset.lifecycleStatus === 'NORMAL') {
      return (
        <Button type="primary" onClick={() => onAssignOrganization(asset)}>
          永久分配机构
        </Button>
      );
    }
    return null;
  }, [
    asset,
    loadFactoryProgress,
    mode,
    onAssignOrganization,
    onAssignTenant,
    onControl,
    reevaluating,
  ]);

  const openConfigurationEditor = () => {
    if (!latestVersion) return;
    configForm.setFieldsValue({
      reason: '',
      ports: latestVersion.ports.map((port) => ({
        displayName: port.displayName,
        enabled: port.enabled,
      })),
    });
    setConfigurationModalOpen(true);
  };

  const publishConfiguration = async () => {
    if (!asset || !organizationCode || !latestVersion || !canPublishConfiguration) return;
    const edits = await configForm.validateFields();
    const payload = mergeConfiguration(latestVersion, edits);
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey('device.configuration.release', asset.deviceCode, payload),
        (intent) => releaseDeviceConfiguration(
          organizationCode,
          asset.deviceCode,
          payload,
          intent,
        ),
      );
      message.success('新配置已发布，设备联网后会自动应用');
      setConfigurationModalOpen(false);
      await loadConfiguration();
      onChanged();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const openPlatformRecovery = (kind: PlatformRecoveryKind) => {
    recoveryForm.setFieldsValue({ reason: '' });
    setRecoveryKind(kind);
  };

  const submitPlatformRecovery = async () => {
    if (
      !asset
      || mode !== 'platform'
      || !latestVersion
      || !recoveryKind
    ) return;
    const { reason } = await recoveryForm.validateFields();
    setRecoverySubmitting(true);
    try {
      if (recoveryKind === 'roll-forward') {
        const payload = {
          expectedLatestVersion: latestVersion.versionNo,
          reason,
        };
        await executeCommand(
          commandKey(
            'device.configuration.roll-forward',
            asset.hardwareSn,
            payload,
          ),
          (intent) => rollForwardPlatformDeviceConfiguration(
            asset.hardwareSn,
            payload,
            intent,
          ),
        );
        message.success(
          `已生成 v${latestVersion.versionNo + 1} 并进入自动下发`,
        );
      } else {
        if (!latestApplication) return;
        const payload = {
          expectedVersion: latestApplication.version,
          reason,
        };
        await executeCommand(
          commandKey(
            'device.configuration.resynchronize',
            `${asset.hardwareSn}:${latestApplication.applicationUid}`,
            payload,
          ),
          (intent) => resynchronizePlatformDeviceConfiguration(
            asset.hardwareSn,
            latestApplication.applicationUid,
            payload,
            intent,
          ),
        );
        message.success(`已重新提交 v${latestVersion.versionNo} 下发任务`);
      }
      setRecoveryKind(undefined);
      await Promise.all([loadConfiguration(), loadTechnicalIssues()]);
      onChanged();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setRecoverySubmitting(false);
    }
  };

  const openManualBaselineAttempt = (issue: DeviceTechnicalIssue) => {
    if (issue.portNo == null || !issue.latestMeasurementUid) return;
    baselineForm.setFieldsValue({
      causeFixedConfirmed: false,
      emptyBagConfirmed: false,
      reason: '',
    });
    setBaselineIssue(issue);
  };

  const submitManualBaselineAttempt = async () => {
    if (
      !asset
      || !baselineIssue
      || baselineIssue.portNo == null
      || !baselineIssue.latestMeasurementUid
    ) return;
    const values = await baselineForm.validateFields();
    const payload = {
      expectedLatestMeasurementUid: baselineIssue.latestMeasurementUid,
      causeFixedConfirmed: true as const,
      emptyBagConfirmed: true as const,
      reason: values.reason,
    };
    setBaselineSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'device.baseline.manual-attempt',
          `${asset.hardwareSn}:${baselineIssue.portNo}`,
          payload,
        ),
        (intent) => startPlatformBaselineMeasurementAttempt(
          asset.hardwareSn,
          baselineIssue.portNo!,
          payload,
          intent,
        ),
      );
      message.success(
        `已安排新一轮空袋重量测量，等待设备执行（${baselineIssue.portNo} 号投口）`,
      );
      setBaselineIssue(undefined);
      await loadTechnicalIssues();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setBaselineSubmitting(false);
    }
  };

  const openDeliveryNotStartedConfirmation = (
    issue: DeviceTechnicalIssue,
  ) => {
    if (
      !issue.taskUid
      || !issue.deliverySessionUid
      || issue.deliverySessionVersion == null
    ) return;
    deliveryRecoveryForm.setFieldsValue({
      causeFixedConfirmed: false,
      deliveryNeverStartedConfirmed: false,
      reason: '',
    });
    setDeliveryRecoveryIssue(issue);
  };

  const submitDeliveryNotStartedConfirmation = async () => {
    if (
      !asset
      || !deliveryRecoveryIssue?.taskUid
      || !deliveryRecoveryIssue.deliverySessionUid
      || deliveryRecoveryIssue.deliverySessionVersion == null
    ) return;
    const values = await deliveryRecoveryForm.validateFields();
    const payload = {
      expectedTaskUid: deliveryRecoveryIssue.taskUid,
      expectedSessionVersion:
        deliveryRecoveryIssue.deliverySessionVersion,
      causeFixedConfirmed: true as const,
      deliveryNeverStartedConfirmed: true as const,
      reason: values.reason,
    };
    setDeliveryRecoverySubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'device.delivery.confirm-not-started',
          `${asset.hardwareSn}:${deliveryRecoveryIssue.deliverySessionUid}`,
          payload,
        ),
        (intent) => confirmPlatformDeliveryNotStarted(
          asset.hardwareSn,
          deliveryRecoveryIssue.deliverySessionUid!,
          payload,
          intent,
        ),
      );
      message.success('原投递已安全结束；请让用户重新扫码发起投递');
      setDeliveryRecoveryIssue(undefined);
      await Promise.all([loadTechnicalIssues(), loadRuntime()]);
      onChanged();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setDeliveryRecoverySubmitting(false);
    }
  };

  const openDeliveryRecoveryQuarantine = (issue: DeviceTechnicalIssue) => {
    if (
      !issue.taskUid
      || !issue.deliverySessionUid
      || issue.deliverySessionVersion == null
    ) return;
    deliveryQuarantineForm.setFieldsValue({
      physicalOutcomeUnknownConfirmed: false,
      causeFixedConfirmed: false,
      devicePowerCycledConfirmed: false,
      motionAreaClearConfirmed: false,
      deliveryDoorClosedConfirmed: false,
      mechanismClearConfirmed: false,
      reason: '',
    });
    setDeliveryQuarantineIssue(issue);
  };

  const submitDeliveryRecoveryQuarantine = async () => {
    if (
      !asset
      || !deliveryQuarantineIssue?.taskUid
      || !deliveryQuarantineIssue.deliverySessionUid
      || deliveryQuarantineIssue.deliverySessionVersion == null
    ) return;
    const values = await deliveryQuarantineForm.validateFields();
    const payload = {
      expectedTaskUid: deliveryQuarantineIssue.taskUid,
      expectedSessionVersion:
        deliveryQuarantineIssue.deliverySessionVersion,
      physicalOutcomeUnknownConfirmed: true as const,
      causeFixedConfirmed: true as const,
      devicePowerCycledConfirmed: true as const,
      motionAreaClearConfirmed: true as const,
      deliveryDoorClosedConfirmed: true as const,
      mechanismClearConfirmed: true as const,
      reason: values.reason,
    };
    setDeliveryQuarantineSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'device.delivery.quarantine-recovery',
          `${asset.hardwareSn}:${deliveryQuarantineIssue.deliverySessionUid}`,
          payload,
        ),
        (intent) => quarantinePlatformDeliveryRecovery(
          asset.hardwareSn,
          deliveryQuarantineIssue.deliverySessionUid!,
          payload,
          intent,
        ),
      );
      message.success('异常隔离指令已下发；设备确认前仍保持原占用且不会产生订单或余额');
      setDeliveryQuarantineIssue(undefined);
      await Promise.all([loadTechnicalIssues(), loadRuntime()]);
      onChanged();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setDeliveryQuarantineSubmitting(false);
    }
  };

  const openAbnormalDeliveryTermination = (issue: DeviceTechnicalIssue) => {
    if (
      !issue.taskUid
      || !issue.deliverySessionUid
      || issue.deliverySessionVersion == null
    ) return;
    abnormalTerminationForm.setFieldsValue({
      physicalOutcomeUnknownConfirmed: false,
      devicePoweredOffConfirmed: false,
      motionAreaClearConfirmed: false,
      deliveryDoorClosedConfirmed: false,
      mechanismClearConfirmed: false,
      reason: '',
    });
    setAbnormalTerminationIssue(issue);
  };

  const submitAbnormalDeliveryTermination = async () => {
    if (
      !asset
      || !abnormalTerminationIssue?.taskUid
      || !abnormalTerminationIssue.deliverySessionUid
      || abnormalTerminationIssue.deliverySessionVersion == null
    ) return;
    const values = await abnormalTerminationForm.validateFields();
    const payload = {
      expectedTaskUid: abnormalTerminationIssue.taskUid,
      expectedSessionVersion:
        abnormalTerminationIssue.deliverySessionVersion,
      expectedAssetVersion: asset.version,
      physicalOutcomeUnknownConfirmed: true as const,
      devicePoweredOffConfirmed: true as const,
      motionAreaClearConfirmed: true as const,
      deliveryDoorClosedConfirmed: true as const,
      mechanismClearConfirmed: true as const,
      reason: values.reason,
    };
    setAbnormalTerminationSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'device.delivery.abnormal-terminate',
          `${asset.hardwareSn}:${abnormalTerminationIssue.deliverySessionUid}`,
          payload,
        ),
        (intent) => terminatePlatformAbnormalDelivery(
          asset.hardwareSn,
          abnormalTerminationIssue.deliverySessionUid!,
          payload,
          intent,
        ),
      );
      message.success('异常投递已结束，占用已解除，设备已禁用；可另行恢复或报废');
      setAbnormalTerminationIssue(undefined);
      await Promise.all([loadTechnicalIssues(), loadRuntime()]);
      onChanged();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setAbnormalTerminationSubmitting(false);
    }
  };

  const openDeliveryRecoveryEvidence = async (issue: DeviceTechnicalIssue) => {
    if (!asset) return;
    setDeliveryQuarantineEvidence(undefined);
    setDeliveryQuarantineEvidenceIssue(issue);
    setDeliveryQuarantineEvidenceLoading(true);
    try {
      setDeliveryQuarantineEvidence(
        await getPlatformDeliveryRecoveryQuarantine(
          asset.hardwareSn,
          issue.issueUid,
        ),
      );
    } catch (error) {
      message.error(errorMessage(error));
      setDeliveryQuarantineEvidenceIssue(undefined);
    } finally {
      setDeliveryQuarantineEvidenceLoading(false);
    }
  };

  const reevaluateAcceptanceFromIssue = async () => {
    if (!asset || asset.lifecycleStatus !== 'NORMAL') return;
    setReevaluating(true);
    try {
      await onReevaluateAcceptance(asset);
      await Promise.all([
        loadEvidence(),
        loadTechnicalIssues(),
        loadFactoryProgress(),
      ]);
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setReevaluating(false);
    }
  };

  const issueActions = (issue: DeviceTechnicalIssue) => (
    <Space wrap>
      {asset?.lifecycleStatus === 'NORMAL' && issue.nextActions.includes('REEVALUATE_ACCEPTANCE') && (
        <Button
          size="small"
          loading={reevaluating}
          onClick={() => void reevaluateAcceptanceFromIssue()}
        >
          重新核对设备检查结果
        </Button>
      )}
      {issue.nextActions.includes('RESYNCHRONIZE_CONFIGURATION') && (
        <Button
          size="small"
          disabled={!latestApplication}
          onClick={() => openPlatformRecovery('resynchronize')}
        >
          重新下发当前配置
        </Button>
      )}
      {issue.nextActions.includes('PUBLISH_NEW_CONFIGURATION') && (
        <Button
          size="small"
          type="primary"
          disabled={!latestVersion}
          onClick={() => openPlatformRecovery('roll-forward')}
        >
          发布修复版本
        </Button>
      )}
      {issue.nextActions.includes('START_MANUAL_BASELINE_MEASUREMENT') && (
        <Button
          size="small"
          type="primary"
          onClick={() => openManualBaselineAttempt(issue)}
        >
          现场确认后重新测量
        </Button>
      )}
      {issue.nextActions.includes('CONFIRM_DELIVERY_NOT_STARTED') && (
        <Button
          size="small"
          type="primary"
          danger
          onClick={() => openDeliveryNotStartedConfirmation(issue)}
        >
          确认未开始并结束本次投递
        </Button>
      )}
      {issue.nextActions.includes('QUARANTINE_DELIVERY_RECOVERY') && (
        <Button
          size="small"
          type="primary"
          danger
          onClick={() => openDeliveryRecoveryQuarantine(issue)}
        >
          隔离结束物理结果未知的投递
        </Button>
      )}
      {issue.nextActions.includes('END_ABNORMAL_DELIVERY') && (
        <Button
          size="small"
          type="primary"
          onClick={() => openAbnormalDeliveryTermination(issue)}
        >
          结束异常投递
        </Button>
      )}
      {issue.nextActions.includes('VIEW_DELIVERY_RECOVERY_EVIDENCE') && (
        <Button
          size="small"
          loading={deliveryQuarantineEvidenceLoading
            && deliveryQuarantineEvidenceIssue?.issueUid === issue.issueUid}
          onClick={() => void openDeliveryRecoveryEvidence(issue)}
        >
          查看问题证据
        </Button>
      )}
    </Space>
  );

  return (
    <>
      <Drawer
        width={720}
        open={open}
        onClose={onClose}
        destroyOnClose
        title={
          <Space direction="vertical" size={0}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              设备详情
            </Typography.Text>
            <Typography.Text strong>{asset?.installationProfile.displayName || '设备详情'}</Typography.Text>
          </Space>
        }
        extra={(
          <Space wrap>
            {mode === 'platform' && (
              <Button
                icon={<ToolOutlined />}
                onClick={() => setRemoteSupportOpen(true)}
              >
                远程维护
              </Button>
            )}
            <Button
              icon={<QrcodeOutlined />}
              onClick={() => setEntryQrOpen(true)}
            >
              设备二维码
            </Button>
            {actionButtons}
          </Space>
        )}
      >
        {!asset ? (
          <Empty description="请选择设备" />
        ) : (
          <Space key={mode + hardwareSn} direction="vertical" size={16} style={{ width: '100%' }}>
            <section>
              <Space
                align="center"
                style={{
                  width: '100%',
                  justifyContent: 'space-between',
                  marginBottom: 8,
                }}
              >
                <Typography.Title level={5} style={{ margin: 0 }}>
                  整机数据与状态
                </Typography.Title>
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  loading={loadingRuntime}
                  onClick={() => void loadRuntime()}
                >
                  刷新设备数据
                </Button>
              </Space>
              <Descriptions column={2} size="small" bordered>
                <Descriptions.Item label="设备编号">
                  <Typography.Text copyable>{asset.hardwareSn}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="设备名称">
                  {asset.installationProfile.displayName}
                </Descriptions.Item>
                <Descriptions.Item label="所属机构">
                  {asset.organizationCode ?? '尚未分配'}
                </Descriptions.Item>
                <Descriptions.Item label="使用状态">
                  <Tag color={assetColors[asset.lifecycleStatus]}>
                    {assetLabels[asset.lifecycleStatus]}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="安装地址" span={2}>
                  {asset.installationProfile.address ?? '尚未设置'}
                </Descriptions.Item>
                <Descriptions.Item label="联网状态">
                  <Tag color={runtimeStatusColor(
                    runtimeLoad.data?.health.oneNetConnectionStatus,
                  )}>
                    {runtimeStatusLabel(
                      runtimeLoad.data?.health.oneNetConnectionStatus,
                    )}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="设备状况">
                  <Tag color={deviceCondition.color}>{deviceCondition.label}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="当前投口总重量">
                  {totalPortWeightText(runtimeLoad.data)}
                </Descriptions.Item>
                <Descriptions.Item label="整机满溢状态">
                  {deviceFullnessText(runtimeLoad.data)}
                </Descriptions.Item>
                <Descriptions.Item label="最近成功投递">
                  {optionalTime(runtimeLoad.data?.lastDeliveryAt)}
                </Descriptions.Item>
                <Descriptions.Item label="最近成功清运">
                  {optionalTime(runtimeLoad.data?.lastCleanAt)}
                </Descriptions.Item>
                <Descriptions.Item label="当前配置">
                  {configurationVersionText(runtimeLoad.data)}
                </Descriptions.Item>
                <Descriptions.Item label="当前作业">
                  {runtimeLoad.data?.occupied ? (
                    <Tag color="processing">
                      {runtimeStatusLabel(runtimeLoad.data.occupancyKind)}
                    </Tag>
                  ) : '空闲'}
                </Descriptions.Item>
              </Descriptions>
            </section>

            <DeviceManagementAttention
              management={softwareManagement}
            />

            {asset.lifecycleStatus === 'DISABLED' && <Alert showIcon type="info" message="设备已禁用，配置和更新待办已暂停，启用后继续处理。" />}
            {configurationError && <Alert showIcon type="warning" message="配置读取失败" description={configurationError} action={<Button size="small" onClick={() => void loadConfiguration()}>重试</Button>} />}
            {latestApplication?.status === 'FAILED' && <Alert type="error" showIcon message="配置应用失败" description="新配置尚未生效，请先处理配置问题。" action={<Button size="small" onClick={() => setConfigurationExpanded(true)}>处理配置问题</Button>} />}

            {mode === 'platform' && (technicalIssues.length > 0 || technicalIssueLoad.status === 'error' || !technicalIssueLoad.hasLoaded) && (
              <section>
                <Space
                  align="center"
                  style={{
                    width: '100%',
                    justifyContent: 'space-between',
                    marginBottom: 12,
                  }}
                >
                  <Space>
                    <SafetyCertificateOutlined />
                    <Typography.Title level={5} style={{ margin: 0 }}>
                      设备问题与安全恢复
                    </Typography.Title>
                  </Space>
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    loading={loadingTechnicalIssues}
                    onClick={() => void loadTechnicalIssues()}
                  >
                    刷新
                  </Button>
                </Space>
                <Spin spinning={loadingTechnicalIssues}>
                  {technicalIssueLoad.status === 'error' && (
                    <Alert
                      type={technicalIssueLoad.hasLoaded ? 'warning' : 'error'}
                      showIcon
                      style={{ marginBottom: technicalIssues.length ? 12 : 0 }}
                      message={technicalIssueLoad.hasLoaded
                        ? '设备问题刷新失败'
                        : '设备问题加载失败'}
                      description={(
                        <Space direction="vertical" size={8}>
                          <Typography.Text>
                            {technicalIssueLoad.hasLoaded
                              ? '以下内容可能已过期，请重试后再判断设备当前状态。'
                              : '暂时无法判断设备是否健康，请重试加载。'}
                          </Typography.Text>
                          {technicalIssueLoad.error && (
                            <Typography.Text type="secondary">
                              {technicalIssueLoad.error}
                            </Typography.Text>
                          )}
                          <Button
                            size="small"
                            onClick={() => void loadTechnicalIssues()}
                          >
                            重试加载
                          </Button>
                        </Space>
                      )}
                    />
                  )}
                  {technicalIssueLoad.hasLoaded
                    && technicalIssues.length > 0 && (
                      <List
                        split={false}
                        dataSource={technicalIssues}
                        renderItem={(issue) => (
                          <List.Item style={{ padding: '6px 0' }}>
                            <Alert
                              style={{ width: '100%' }}
                              showIcon
                              type={issue.severity === 'CRITICAL'
                                ? 'error'
                                : issue.severity === 'WARNING'
                                  ? 'warning'
                                  : 'info'}
                              message={(
                                <Space wrap>
                                  <Typography.Text strong>
                                    {operatorFacingTechnicalText(issue.title)}
                                  </Typography.Text>
                                  <Tag>{technicalIssueStateLabel(issue.state)}</Tag>
                                  {issue.portNo != null && (
                                    <Tag>{issue.portNo} 号投口</Tag>
                                  )}
                                </Space>
                              )}
                              description={(
                                <Space
                                  direction="vertical"
                                  size={8}
                                  style={{ width: '100%' }}
                                >
                                  <Typography.Text>
                                    {operatorFacingTechnicalText(issue.description)}
                                  </Typography.Text>
                                  <Typography.Text
                                    type="secondary"
                                    style={{ fontSize: 12 }}
                                  >
                                    {issue.automaticAttemptNo != null
                                      && issue.automaticAttemptLimit != null
                                      ? `系统自动处理：第 ${issue.automaticAttemptNo} 次，最多 ${issue.automaticAttemptLimit} 次`
                                      : ''}
                                    {issue.occurredAt
                                      ? `${issue.automaticAttemptNo != null ? ' · ' : ''}记录时间：${formatShanghaiTime(issue.occurredAt)}`
                                      : ''}
                                  </Typography.Text>
                                  <Collapse
                                    ghost
                                    size="small"
                                    items={[{
                                      key: `technical-issue-${issue.issueUid}`,
                                      label: '技术诊断（报修时使用）',
                                      children: (
                                        <Descriptions size="small" column={1}>
                                          <Descriptions.Item label="问题代码">
                                            <Typography.Text code>{issue.code}</Typography.Text>
                                          </Descriptions.Item>
                                          <Descriptions.Item label="问题记录编号">
                                            <Typography.Text copyable>
                                              {issue.issueUid}
                                            </Typography.Text>
                                          </Descriptions.Item>
                                          {issue.taskUid && (
                                            <Descriptions.Item label="后台任务编号">
                                              <Typography.Text copyable>
                                                {issue.taskUid}
                                              </Typography.Text>
                                            </Descriptions.Item>
                                          )}
                                          {issue.blockedReasonCode && (
                                            <Descriptions.Item label="阻断原因代码">
                                              <Typography.Text code>
                                                {issue.blockedReasonCode}
                                              </Typography.Text>
                                            </Descriptions.Item>
                                          )}
                                          {issue.externalErrorCode && (
                                            <Descriptions.Item label="外部服务错误代码">
                                              <Typography.Text code>
                                                {issue.externalErrorCode}
                                              </Typography.Text>
                                            </Descriptions.Item>
                                          )}
                                          {issue.httpStatus != null && (
                                            <Descriptions.Item label="网络响应状态">
                                              {issue.httpStatus}
                                            </Descriptions.Item>
                                          )}
                                          {issue.diagnostic && (
                                            <Descriptions.Item label="诊断详情">
                                              <Typography.Text code>
                                                {issue.diagnostic}
                                              </Typography.Text>
                                            </Descriptions.Item>
                                          )}
                                        </Descriptions>
                                      ),
                                    }]}
                                  />
                                  {issueActions(issue)}
                                </Space>
                              )}
                            />
                          </List.Item>
                        )}
                      />
                    )}
                  {loadingTechnicalIssues
                    && !technicalIssueLoad.hasLoaded && (
                      <div style={{ minHeight: 56 }} aria-label="正在加载设备问题" />
                    )}
                </Spin>
              </section>
            )}

            {mode === 'platform' && asset.lifecycleStatus !== 'RETIRED' && !factoryProgressCompleted(factoryProgressLoad.data) && (
              <FactoryProgressPanel
                retired={false}
                load={factoryProgressLoad}
                onRefresh={() => void loadFactoryProgress()}
              />
            )}

            <section>
              <Space
                align="center"
                style={{
                  width: '100%',
                  justifyContent: 'space-between',
                  marginBottom: 8,
                }}
              >
                <Space>
                  <MonitorOutlined />
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    投口数据与状态
                  </Typography.Title>
                </Space>
              </Space>
              <Spin spinning={loadingRuntime && !runtimeLoad.hasLoaded}>
                {runtimeLoad.status === 'error' && (
                  <Alert
                    type={runtimeLoad.hasLoaded ? 'warning' : 'error'}
                    showIcon
                    style={{ marginBottom: runtimeLoad.data ? 12 : 0 }}
                    message={runtimeLoad.hasLoaded
                      ? '设备状态刷新失败，以下是上一次成功结果'
                      : '设备状态加载失败'}
                    description={runtimeLoad.error}
                    action={(
                      <Button size="small" onClick={() => void loadRuntime()}>
                        重试
                      </Button>
                    )}
                  />
                )}
                {runtimeLoad.data ? (
                  <RuntimePortsPanel runtime={runtimeLoad.data} />
                ) : loadingRuntime ? (
                  <div
                    aria-label="正在加载设备当前状态"
                    style={{ minHeight: 96 }}
                  />
                ) : runtimeLoad.status !== 'error' ? (
                  <Empty description="尚未读取设备当前状态" />
                ) : null}
              </Spin>
            </section>

            <section>
              <Space
                align="center"
                style={{
                  width: '100%',
                  justifyContent: 'space-between',
                  marginBottom: 8,
                }}
              >
                <Space>
                  <CloudSyncOutlined />
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    设备配置
                  </Typography.Title>
                </Space>
                {canConfigure && (
                  <Button
                    size="small"
                    onClick={() => setConfigurationExpanded(value => !value)}
                  >
                    {configurationExpanded ? '收起设备配置' : '查看设备配置'}
                  </Button>
                )}
              </Space>
              <Descriptions size="small" bordered column={2}>
                <Descriptions.Item label="设备已应用版本">
                  {runtimeLoad.data?.configuration.latestAppliedVersion == null
                    ? '尚未应用'
                    : `V${runtimeLoad.data.configuration.latestAppliedVersion}`}
                </Descriptions.Item>
                <Descriptions.Item label="最新发布版本">
                  {runtimeLoad.data?.configuration.latestPublishedVersion == null
                    ? '尚无配置'
                    : `V${runtimeLoad.data.configuration.latestPublishedVersion}`}
                </Descriptions.Item>
                <Descriptions.Item label="应用结果" span={2}>
                  {runtimeLoad.data?.configuration.latestApplicationStatus ? (
                    <Tag color={configurationColors[
                      runtimeLoad.data.configuration.latestApplicationStatus
                    ]}>
                      {configurationLabels[
                        runtimeLoad.data.configuration.latestApplicationStatus
                      ]}
                    </Tag>
                  ) : '尚无应用记录'}
                </Descriptions.Item>
              </Descriptions>
              {!asset.organizationCode && (
                <Alert
                  style={{ marginTop: 8 }}
                  type="info"
                  showIcon
                  message="永久分配机构后才会生成设备配置"
                />
              )}
              {canConfigure && configurationExpanded && (
                <Card size="small" style={{ marginTop: 8 }}>
                    <div>
                      <Space
                        align="center"
                        style={{ width: '100%', justifyContent: 'space-between' }}
                      >
                        <Space>
                          <CloudSyncOutlined />
                          <Typography.Title level={5} style={{ margin: 0 }}>
                            {mode === 'platform'
                              ? '配置下发与恢复'
                              : '投口设置与配置记录'}
                          </Typography.Title>
                        </Space>
                        {mode === 'platform' ? (
                          <Space wrap>
                            <Button
                              icon={<ReloadOutlined />}
                              disabled={
                                !latestApplication?.nextActions.includes(
                                  'RESYNCHRONIZE',
                                )
                              }
                              onClick={() => openPlatformRecovery('resynchronize')}
                            >
                              重新下发当前版本
                            </Button>
                            <Button
                              type="primary"
                              icon={<CloudSyncOutlined />}
                              disabled={
                                !latestVersion
                                || asset.lifecycleStatus !== 'NORMAL'
                                || !latestApplication?.nextActions.includes(
                                  'PUBLISH_NEW_CONFIGURATION',
                                )
                              }
                              onClick={() => openPlatformRecovery('roll-forward')}
                            >
                              发布修复版本
                            </Button>
                          </Space>
                        ) : canPublishConfiguration ? (
                          <Button
                            icon={<EditOutlined />}
                            disabled={!latestVersion || asset.lifecycleStatus !== 'NORMAL'}
                            onClick={openConfigurationEditor}
                          >
                            基于最新版发布
                          </Button>
                        ) : null}
                      </Space>
                      <Alert
                        style={{ margin: '12px 0' }}
                        type={mode === 'platform' ? 'warning' : 'info'}
                        showIcon
                        message={mode === 'platform'
                          ? '两种恢复操作处理的问题不同'
                          : '安装、通电和联网后无需机构确认'}
                        description={mode === 'platform'
                          ? '“重新下发”只适用于指令尚未到达设备的情况；同一版本的配置内容与记录不一致、设备已经接收，或应用已经明确失败时，排除故障后都必须发布更高的修复版本。'
                          : '系统会自动下发配置并测量厂家初始袋皮重；这里仅用于日常改价或调整投口配置。'}
                      />
                      {mode === 'platform'
                        && latestApplication?.status === 'FAILED'
                        && latestApplication.lastFailureCode && (
                          <Alert
                            style={{ marginBottom: 12 }}
                            type="error"
                            showIcon
                            message="最近一次配置应用未成功"
                            description={(
                              <Space direction="vertical" size={6}>
                                <Typography.Text>
                                  请先排除设备连接或配置问题，再按页面建议发布修复版本。
                                </Typography.Text>
                                <Collapse
                                  ghost
                                  size="small"
                                  items={[{
                                    key: 'configuration-failure-diagnostic',
                                    label: '技术诊断（报修时使用）',
                                    children: (
                                      <Descriptions size="small" column={1}>
                                        <Descriptions.Item label="失败代码">
                                          <Typography.Text code>
                                            {latestApplication.lastFailureCode}
                                          </Typography.Text>
                                        </Descriptions.Item>
                                        {latestApplication.lastFailedAt && (
                                          <Descriptions.Item label="失败记录时间">
                                            {formatShanghaiTime(latestApplication.lastFailedAt)}
                                          </Descriptions.Item>
                                        )}
                                      </Descriptions>
                                    ),
                                  }]}
                                />
                              </Space>
                            )}
                          />
                        )}
                      <Spin spinning={loadingConfiguration}>
                        {!versions.length ? (
                          <Empty description={mode === 'platform'
                            ? '尚未找到配置版本'
                            : '系统正在创建并下发初始配置'} />
                        ) : (
                          <List
                            dataSource={versions}
                            renderItem={(version) => (
                              <List.Item>
                                <List.Item.Meta
                                  title={
                                    <Space>
                                      <Typography.Text strong>
                                        v{version.versionNo}
                                      </Typography.Text>
                                      <Tag color={configurationColors[version.application.status]}>
                                        {configurationLabels[version.application.status]}
                                      </Tag>
                                      <Tag>
                                        {taskStateLabel(version.application.dispatchState)}
                                      </Tag>
                                    </Space>
                                  }
                                  description={
                                    `${version.publishedBy}`
                                    + ` · ${formatShanghaiTime(version.publishedAt)}`
                                  }
                                />
                              </List.Item>
                            )}
                          />
                        )}
                      </Spin>
                    </div>
                </Card>
              )}
            </section>
            <Collapse size="small" items={[{
              key: 'detail-data', label: '细节数据', children: (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    设备标识与安装记录
                  </Typography.Title>
                  <Descriptions column={2} bordered size="small">
                    <Descriptions.Item label="设备公开码" span={2}>
                      <Typography.Text copyable code>{asset.deviceCode}</Typography.Text>
                    </Descriptions.Item>
                    <Descriptions.Item label="型号">{asset.modelCode}</Descriptions.Item>
                    <Descriptions.Item label="安装资料版本">
                      V{asset.installationProfile.version}
                      {asset.installationProfile.complete ? (
                        <Tag color="success" style={{ marginLeft: 8 }}>完整</Tag>
                      ) : (
                        <Tag color="warning" style={{ marginLeft: 8 }}>待完善</Tag>
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="安装坐标" span={2}>
                      {asset.installationProfile.longitude
                        && asset.installationProfile.latitude
                        ? `${asset.installationProfile.longitude}, `
                        + `${asset.installationProfile.latitude} `
                        + `(${asset.installationProfile.coordinateSystem})`
                        : '尚未设置'}
                    </Descriptions.Item>
                    <Descriptions.Item label="安装资料更新时间" span={2}>
                      {formatShanghaiTime(asset.installationProfile.updatedAt)}
                    </Descriptions.Item>
                    <Descriptions.Item label="物联网平台设备名称" span={2}>
                      {asset.oneNetMapping.deviceName}
                    </Descriptions.Item>
                  </Descriptions>
                  {runtimeLoad.data ? (
                    <RuntimeTechnicalDetails runtime={runtimeLoad.data} />
                  ) : (
                    <Empty description="尚无运行诊断记录" />
                  )}
                  <DeviceManagementTechnicalDetails management={softwareManagement} />
                  {mode === 'platform'
                    && (asset.lifecycleStatus === 'RETIRED'
                      || factoryProgressCompleted(factoryProgressLoad.data)) && (
                      <FactoryProgressPanel
                        retired={asset.lifecycleStatus === 'RETIRED'}
                        load={factoryProgressLoad}
                        onRefresh={() => void loadFactoryProgress()}
                      />
                    )}
                  {mode === 'platform' && (
                    <Collapse
                      activeKey={evidenceExpanded ? ['acceptance-evidence'] : []}
                      onChange={(keys) => {
                        const expanded = Array.isArray(keys)
                          ? keys.includes('acceptance-evidence')
                          : keys === 'acceptance-evidence';
                        setEvidenceExpanded(expanded);
                        if (expanded && !evidenceLoaded && !loadingEvidence) {
                          void loadEvidence();
                        }
                      }}
                      items={[{
                        key: 'acceptance-evidence',
                        label: (
                          <Space wrap>
                            <HistoryOutlined />
                            <Typography.Text strong>
                              设备检查历史记录
                            </Typography.Text>
                            {evidenceLoaded && <Tag>{evidence.length} 份记录</Tag>}
                          </Space>
                        ),
                        children: (
                          <Space
                            direction="vertical"
                            size={12}
                            style={{ width: '100%' }}
                          >
                            <Alert
                              type="info"
                              showIcon
                              message="这里展示的是以往检查记录，不代表设备当前状态"
                            />
                            <Spin spinning={loadingEvidence}>
                              <EvidencePanel rows={evidence} />
                            </Spin>
                          </Space>
                        ),
                      }]}
                    />
                  )}
                </Space>
              )
            }]} />
          </Space>
        )}
      </Drawer>
      <Modal title="设备二维码" open={entryQrOpen} onCancel={() => setEntryQrOpen(false)} footer={null} destroyOnClose>
        {asset?.deviceEntryUrl ? (
          <Space direction="vertical" size={12}>
            <Typography.Text copyable={{ text: asset.deviceEntryUrl }}>
              {asset.deviceEntryUrl}
            </Typography.Text>
            {entryQrError ? (
              <Typography.Text type="danger">
                二维码生成失败，请刷新页面重试
              </Typography.Text>
            ) : entryQrDataUrl ? (
              <Space align="end" size={16}>
                <img
                  src={entryQrDataUrl}
                  width={160}
                  height={160}
                  alt={`${asset.hardwareSn} 设备入口二维码`}
                  style={{ border: '1px solid #edf0ed', borderRadius: 8 }}
                />
                <Button
                  icon={<DownloadOutlined />}
                  onClick={downloadEntryQr}
                >
                  下载二维码
                </Button>
              </Space>
            ) : (
              <Space>
                <QrcodeOutlined />
                <Typography.Text type="secondary">
                  正在本地生成二维码…
                </Typography.Text>
              </Space>
            )}
          </Space>
        ) : (
          <Typography.Text type="secondary">
            机构尚未绑定可用小程序渠道，暂不能生成入口二维码
          </Typography.Text>
        )}

      </Modal>

      <Modal
        width={820}
        title="远程维护"
        open={remoteSupportOpen}
        onCancel={() => setRemoteSupportOpen(false)}
        footer={null}
        destroyOnClose
      >
        {asset && <RemoteSupportPanel hardwareSn={asset.hardwareSn} />}
      </Modal>

      <Modal
        width={720}
        title="结束异常投递"
        open={Boolean(abnormalTerminationIssue)}
        confirmLoading={abnormalTerminationSubmitting}
        onOk={() => void submitAbnormalDeliveryTermination()}
        onCancel={() => setAbnormalTerminationIssue(undefined)}
        okText="确认结束异常投递"
        okButtonProps={{ danger: true }}
        destroyOnClose
      >
        <Alert
          type="warning"
          showIcon
          message="原投递不会生成订单或返现；设备将进入禁用状态，恢复与报废需另行操作"
          style={{ marginBottom: 20 }}
        />
        <Form form={abnormalTerminationForm} layout="vertical">
          <Form.Item
            name="physicalOutcomeUnknownConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请确认原投递结果无法可靠判断')),
            }]}
          >
            <Checkbox>原投递结果无法可靠判断，不将其认定为成功</Checkbox>
          </Form.Item>
          <Form.Item
            name="devicePoweredOffConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请确认整机已断电')),
            }]}
          >
            <Checkbox>整机已经断电，旧投递不会继续执行</Checkbox>
          </Form.Item>
          <Form.Item
            name="deliveryDoorClosedConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请确认投递门已关闭')),
            }]}
          >
            <Checkbox>投递门已经完全关闭</Checkbox>
          </Form.Item>
          <Form.Item
            name="mechanismClearConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请确认机构内没有卡物')),
            }]}
          >
            <Checkbox>机构内没有卡物或其他阻碍</Checkbox>
          </Form.Item>
          <Form.Item
            name="motionAreaClearConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请确认运动范围内无人')),
            }]}
          >
            <Checkbox>机构运动范围内无人</Checkbox>
          </Form.Item>
          <Form.Item
            name="reason"
            label="异常经过和结束原因"
            rules={[{ required: true, message: '请填写处理原因' }]}
          >
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        width={760}
        title="发布投口设置与配置记录"
        open={configurationModalOpen}
        confirmLoading={submitting}
        onOk={() => void publishConfiguration()}
        onCancel={() => setConfigurationModalOpen(false)}
        okText="发布并自动下发"
      >
        <Alert
          type="warning"
          showIcon
          message="新配置应用后用于新业务，进行中的投递和清运继续使用原配置。"
          style={{ marginBottom: 20 }}
        />
        <Form form={configForm} layout="vertical">
          <Form.Item
            name="reason"
            label="修改原因"
            rules={[{ required: true, message: '请说明为什么修改配置' }]}
          >
            <Input.TextArea maxLength={500} showCount rows={2} />
          </Form.Item>
          <Divider orientation="left">投口日常参数</Divider>
          {latestVersion?.ports.map((port, index) => (
            <Card
              key={port.portNo}
              size="small"
              title={`${port.portNo} 号投口`}
              style={{ marginBottom: 12 }}
              extra={
                <Form.Item
                  name={['ports', index, 'enabled']}
                  valuePropName="checked"
                  noStyle
                >
                  <Switch checkedChildren="启用" unCheckedChildren="停用" />
                </Form.Item>
              }
            >
              <Space size={16} align="start" wrap>
                <Form.Item
                  name={['ports', index, 'displayName']}
                  label="显示名"
                  rules={[{ required: true }]}
                >
                  <Input maxLength={32} style={{ width: 180 }} />
                </Form.Item>
                <Typography.Text type="secondary">
                  单价 {port.unitPriceYuanPerKg} 元/千克
                  <HelpTip label="设备单价来源">单价由配置管理中的设备配置统一设置。</HelpTip>
                </Typography.Text>
              </Space>
            </Card>
          ))}
        </Form>
      </Modal>

      <Modal
        title={recoveryKind === 'roll-forward'
          ? `发布 v${(latestVersion?.versionNo ?? 0) + 1} 修复版本`
          : `重新下发 v${latestVersion?.versionNo ?? '-'}`}
        open={Boolean(recoveryKind)}
        confirmLoading={recoverySubmitting}
        onOk={() => void submitPlatformRecovery()}
        onCancel={() => setRecoveryKind(undefined)}
        okText={recoveryKind === 'roll-forward'
          ? '生成新版本并下发'
          : '确认重新下发'}
        destroyOnClose
      >
        <Alert
          type={recoveryKind === 'roll-forward' ? 'warning' : 'info'}
          showIcon
          message={recoveryKind === 'roll-forward'
            ? '完整复制当前配置，只增加版本号'
            : '不会产生新版本'}
          description={recoveryKind === 'roll-forward'
            ? '用于当前配置已经失败，或指令送达后长时间没有收到设备确认的情况。机构价格、投口和传感器参数不会被平台重新填写或修改。'
            : '仅用于后台发送记录明确证明指令尚未到达设备的情况；不会重新执行已经被设备接收的指令。'}
          style={{ marginBottom: 20 }}
        />
        <Form form={recoveryForm} layout="vertical">
          <Form.Item
            name="reason"
            label="操作原因"
            rules={[{ required: true, message: '请说明本次配置恢复原因' }]}
          >
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="结束未实际开始的投递"
        open={Boolean(deliveryRecoveryIssue)}
        confirmLoading={deliveryRecoverySubmitting}
        onOk={() => void submitDeliveryNotStartedConfirmation()}
        onCancel={() => setDeliveryRecoveryIssue(undefined)}
        okText="确认未开始并结束本次投递"
        okButtonProps={{ danger: true }}
        destroyOnClose
      >
        <Alert
          type="warning"
          showIcon
          message="此操作不会再次开门，也不会补建投递订单"
          description="后台会保留原失败记录，结束原投递并释放设备占用。只有现场确认投递门从未打开、设备也从未进入本次投递时才能继续；完成后请让用户重新扫码发起。若门曾打开或你不能确定，请取消并联系技术人员。"
          style={{ marginBottom: 20 }}
        />
        <Form form={deliveryRecoveryForm} layout="vertical">
          <Form.Item
            name="causeFixedConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认已排除设备无响应原因')),
            }]}
          >
            <Checkbox>我已检查并排除设备无响应的原因</Checkbox>
          </Form.Item>
          <Form.Item
            name="deliveryNeverStartedConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认本次投递从未实际开始')),
            }]}
          >
            <Checkbox>
              我在现场确认投递门从未打开，设备也未进入本次投递
            </Checkbox>
          </Form.Item>
          <Form.Item
            name="reason"
            label="现场检查和处理说明"
            rules={[{ required: true, message: '请记录现场检查和处理结果' }]}
          >
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        width={760}
        title="隔离结束物理结果未知的投递"
        open={Boolean(deliveryQuarantineIssue)}
        confirmLoading={deliveryQuarantineSubmitting}
        onOk={() => void submitDeliveryRecoveryQuarantine()}
        onCancel={() => setDeliveryQuarantineIssue(undefined)}
        okText="确认现场安全并下发隔离指令"
        okButtonProps={{ danger: true }}
        destroyOnClose
      >
        <Alert
          type="error"
          showIcon
          message="这是承认原动作结果未知的隔离操作，不是补报投递成功"
          description="设备上线后只会读取控制板空闲状态、门磁和安全传感器，并核对整机确实经历了新的启动；不会再次发送开门指令。设备确认前仍保留原占用；确认后只结束这一笔异常投递，已有重量和照片仅作为问题记录展示，不会生成投递订单、增加余额、进入审核或触发自动提现。"
          style={{ marginBottom: 20 }}
        />
        <Form form={deliveryQuarantineForm} layout="vertical">
          <Form.Item
            name="physicalOutcomeUnknownConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请确认原投递的物理结果已经无法可靠还原')),
            }]}
          >
            <Checkbox>
              我确认原投递是否实际完成无法可靠判断，不会把它认定为成功投递
            </Checkbox>
          </Form.Item>
          <Form.Item
            name="causeFixedConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认已排除原异常原因')),
            }]}
          >
            <Checkbox>我已检查并排除导致原业务卡住的故障或异常</Checkbox>
          </Form.Item>
          <Form.Item
            name="devicePowerCycledConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认整机已经完全断电并重新通电')),
            }]}
          >
            <Checkbox>
              我已让整机（香橙派和控制板）完全断电，再重新通电启动
            </Checkbox>
          </Form.Item>
          <Form.Item
            name="deliveryDoorClosedConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认投递门已经完全关闭')),
            }]}
          >
            <Checkbox>我在现场确认投递门已经完全关闭</Checkbox>
          </Form.Item>
          <Form.Item
            name="mechanismClearConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认机构内没有卡物')),
            }]}
          >
            <Checkbox>我在现场确认机构没有卡物或其他阻碍</Checkbox>
          </Form.Item>
          <Form.Item
            name="motionAreaClearConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认运动范围内无人')),
            }]}
          >
            <Checkbox>我在现场确认机构运动范围内无人</Checkbox>
          </Form.Item>
          <Form.Item
            name="reason"
            label="异常经过和现场处理说明"
            rules={[{ required: true, message: '请记录异常经过和现场处理结果' }]}
          >
            <Input.TextArea maxLength={500} showCount rows={4} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        width={820}
        title="异常投递的问题证据"
        open={Boolean(deliveryQuarantineEvidenceIssue)}
        footer={null}
        onCancel={() => {
          setDeliveryQuarantineEvidenceIssue(undefined);
          setDeliveryQuarantineEvidence(undefined);
        }}
        destroyOnClose
      >
        <Spin spinning={deliveryQuarantineEvidenceLoading}>
          {deliveryQuarantineEvidence && (
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Alert
                type="info"
                showIcon
                message="该记录的业务价值固定为“无”"
                description="以下重量、照片和诊断内容只用于展示、追查设备问题，不是投递完成凭据，也不会进入订单、余额、审核、退款或提现流程。"
              />
              <Descriptions bordered size="small" column={2}>
                <Descriptions.Item label="隔离记录编号" span={2}>
                  <Typography.Text copyable code>
                    {deliveryQuarantineEvidence.recoveryUid}
                  </Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="投递编号" span={2}>
                  <Typography.Text copyable code>
                    {deliveryQuarantineEvidence.sessionUid}
                  </Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="处理状态">
                  {deliveryQuarantineEvidence.state}
                </Descriptions.Item>
                <Descriptions.Item label="业务价值">
                  <Tag color="default">无</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="投口">
                  {deliveryQuarantineEvidence.portNo == null
                    ? '设备确认前待补充'
                    : `${deliveryQuarantineEvidence.portNo} 号`}
                </Descriptions.Item>
                <Descriptions.Item label="隔离完成时间">
                  {optionalTime(deliveryQuarantineEvidence.appliedAt)}
                </Descriptions.Item>
                <Descriptions.Item label="现场说明" span={2}>
                  {deliveryQuarantineEvidence.reason}
                </Descriptions.Item>
                <Descriptions.Item label="证据摘要" span={2}>
                  {deliveryQuarantineEvidence.evidenceSha256
                    ? (
                      <Typography.Text copyable code>
                        {deliveryQuarantineEvidence.evidenceSha256}
                      </Typography.Text>
                    )
                    : '设备尚未返回终态证据'}
                </Descriptions.Item>
              </Descriptions>
              <Collapse
                items={[
                  {
                    key: 'operator-confirmations',
                    label: '管理员现场确认',
                    children: (
                      <pre style={{
                        margin: 0,
                        maxHeight: 260,
                        overflow: 'auto',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-all',
                      }}>
                        {JSON.stringify(
                          deliveryQuarantineEvidence.operatorConfirmations,
                          null,
                          2,
                        )}
                      </pre>
                    ),
                  },
                  {
                    key: 'device-evidence',
                    label: '设备重启后采集的安全证据',
                    children: (
                      <pre style={{
                        margin: 0,
                        maxHeight: 320,
                        overflow: 'auto',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-all',
                      }}>
                        {JSON.stringify(
                          deliveryQuarantineEvidence.deviceEvidence,
                          null,
                          2,
                        )}
                      </pre>
                    ),
                  },
                  {
                    key: 'existing-data',
                    label: '原业务已存在的数据（仅展示）',
                    children: (
                      <pre style={{
                        margin: 0,
                        maxHeight: 420,
                        overflow: 'auto',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-all',
                      }}>
                        {JSON.stringify(
                          deliveryQuarantineEvidence.existingData,
                          null,
                          2,
                        )}
                      </pre>
                    ),
                  },
                ]}
              />
            </Space>
          )}
        </Spin>
      </Modal>

      <Modal
        title={`${baselineIssue?.portNo ?? '-'} 号投口：重新测量空袋皮重`}
        open={Boolean(baselineIssue)}
        confirmLoading={baselineSubmitting}
        onOk={() => void submitManualBaselineAttempt()}
        onCancel={() => setBaselineIssue(undefined)}
        okText="开始新一轮测量"
        destroyOnClose
      >
        <Alert
          type="warning"
          showIcon
          message="系统不会重复执行上一条失败指令"
          description="系统会保留上次失败记录，并创建全新的空袋重量测量和设备指令。只有现场已排除故障且厂家袋仍为空时才能执行。"
          style={{ marginBottom: 20 }}
        />
        <Form form={baselineForm} layout="vertical">
          <Form.Item
            name="causeFixedConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认已排除上次失败原因')),
            }]}
          >
            <Checkbox>我已检查并排除上次失败原因</Checkbox>
          </Form.Item>
          <Form.Item
            name="emptyBagConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认当前厂家袋为空')),
            }]}
          >
            <Checkbox>我已确认投口中的厂家预装袋仍为空</Checkbox>
          </Form.Item>
          <Form.Item
            name="reason"
            label="现场处理说明"
            rules={[{ required: true, message: '请记录检查和处理结果' }]}
          >
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
