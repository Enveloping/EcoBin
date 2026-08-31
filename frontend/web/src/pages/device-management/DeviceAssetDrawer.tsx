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
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Space,
  Spin,
  Steps,
  Switch,
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
  QrcodeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import QRCode from 'qrcode';
import {
  getDeviceConfigurationVersion,
  getOrganizationDeviceRuntime,
  getPlatformDeviceConfigurationApplication,
  getPlatformDeviceConfigurationVersion,
  getPlatformDeviceFactoryProgress,
  getPlatformDeviceRuntime,
  getTenantDeviceRuntime,
  listDeviceAcceptanceEvidence,
  listDeviceConfigurationVersions,
  listPlatformDeviceConfigurationVersions,
  listPlatformDeviceTechnicalIssues,
  releaseDeviceConfiguration,
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
} from '@/api/deviceDirectory';
import { ApiProblem } from '@/api/request';
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
  connectivityColors,
  connectivityLabels,
  runtimeStatusColor,
  runtimeStatusLabel,
} from './devicePresentation';
import {
  buildFactoryProgressSteps,
  collectFactoryProgressCodes,
  factoryActionLabel,
  factoryFailureGuidance,
  factoryProgressSummary,
  factoryStageLabel,
  sealStatusLabel,
  taskStateLabel,
} from './factoryProgressPresentation';
import RemoteSupportPanel from './RemoteSupportPanel';

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
    unitPriceYuanPerKg?: string;
    fullnessWeightKg?: string;
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
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '设备数据加载失败';
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

function portWeightText(port: DevicePortRuntime): string {
  if (port.weightValueAvailable === false) return '本次没有有效重量';
  if (port.reportedWeightGrams == null) return '尚无重量数据';
  const kind = port.weightValueKind
    ? ` · ${runtimeStatusLabel(port.weightValueKind)}`
    : '';
  return `${port.reportedWeightGrams} 克${kind}`;
}

function PortRuntimePanel({ port }: { port: DevicePortRuntime }) {
  return (
    <Descriptions size="small" bordered column={2}>
      <Descriptions.Item label="投递门状态">
        <RuntimeTag value={port.deliveryDoorState} />
      </Descriptions.Item>
      <Descriptions.Item label="投递门执行器">
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
      <Descriptions.Item label="清运门事实依据">
        {runtimeStatusLabel(port.cleanDoorStateBasis)}
      </Descriptions.Item>
      <Descriptions.Item label="清运员关门确认">
        {port.cleanerPhysicalCloseConfirmed == null
          ? '本次快照未提供'
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
            <Typography.Text>{port.fullnessSensorKind}</Typography.Text>
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
  );
}

function RuntimeStatusPanel({
  runtime,
}: {
  runtime: DeviceRuntime;
}) {
  const { health, configuration } = runtime;
  const online = health.oneNetConnectionStatus;
  const configurationText = configuration.latestPublishedVersion == null
    ? '尚未发布配置'
    : `已发布 v${configuration.latestPublishedVersion}`
      + (configuration.latestAppliedVersion == null
        ? ' · 尚未应用'
        : ` · 设备已应用 v${configuration.latestAppliedVersion}`);
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        showIcon
        type={online === 'ONLINE'
          ? 'success'
          : online === 'OFFLINE'
            ? 'error'
            : 'warning'}
        message={online === 'ONLINE'
          ? 'OneNet 当前报告设备在线'
          : online === 'OFFLINE'
            ? 'OneNet 当前报告设备离线，新投递和清运会被阻止'
            : '尚未取得可信的设备上下线事实'}
        description="在线状态来自 OneNet 上下线通知；其余健康项来自最近一次可信运行快照，必须结合各自时间判断，不代表持续直播值。"
      />
      {!health.trustedRuntimeReceivedAt && (
        <Alert
          type="warning"
          showIcon
          message="尚未收到机构归属后的可信运行快照"
          description="设备可以已经在线，但 MCU、摄像头和传感器等项目仍会显示未知；设备分配机构并上报运行快照后才会形成这些当前投影。"
        />
      )}
      <Descriptions size="small" bordered column={2}>
        <Descriptions.Item label="设备联网">
          <RuntimeTag value={health.oneNetConnectionStatus} />
        </Descriptions.Item>
        <Descriptions.Item label="联网事实时间">
          {optionalTime(health.oneNetStatusObservedAt)}
        </Descriptions.Item>
        <Descriptions.Item label="平台收到联网事实">
          {optionalTime(health.oneNetStatusReceivedAt)}
        </Descriptions.Item>
        <Descriptions.Item label="香橙派最近运行状态">
          <RuntimeTag value={health.edgeConnectionStatus} />
        </Descriptions.Item>
        <Descriptions.Item label="最近运行快照">
          {optionalTime(health.trustedRuntimeReceivedAt)}
        </Descriptions.Item>
        <Descriptions.Item label="MCU 通信">
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
        <Descriptions.Item label="当前配置">
          <Space size={4} wrap>
            <Typography.Text>{configurationText}</Typography.Text>
            {configuration.latestPreciselyApplied && (
              <Tag color="success">精确生效</Tag>
            )}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="香橙派软件">
          {health.edgeSoftwareVersion ?? '尚无数据'}
        </Descriptions.Item>
        <Descriptions.Item label="香橙派启动编号">
          {health.edgeBootId ?? '尚无数据'}
        </Descriptions.Item>
        <Descriptions.Item label="MCU 固件">
          {health.mcuFirmwareVersion ?? '尚无数据'}
        </Descriptions.Item>
        <Descriptions.Item label="MCU 最近重启原因">
          {health.lastMcuResetReason ?? '尚无数据'}
        </Descriptions.Item>
        <Descriptions.Item label="串口状态">
          <Space size={4} wrap>
            <RuntimeTag value={health.uartState} />
            {health.uartProtocolMajor != null
              && health.uartProtocolMinor != null && (
              <Typography.Text type="secondary">
                协议 {health.uartProtocolMajor}.{health.uartProtocolMinor}
              </Typography.Text>
            )}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="待发送可靠事件">
          {health.pendingReliableEventCount ?? '尚无数据'}
        </Descriptions.Item>
        <Descriptions.Item label="香橙派上报配置版本">
          {health.orangePiReportedConfigurationVersion == null
            ? '尚无数据'
            : `v${health.orangePiReportedConfigurationVersion}`}
        </Descriptions.Item>
        <Descriptions.Item label="最近心跳记录">
          {optionalTime(health.lastHeartbeatAt)}
        </Descriptions.Item>
        <Descriptions.Item label="最近设备事件">
          {optionalTime(health.lastDeviceEventAt)}
        </Descriptions.Item>
      </Descriptions>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        页面读取时间：{formatShanghaiTime(runtime.fetchedAt)}。这里是观察视图；真正开始投递或清运时，后端仍会在事务中重新检查全部准入条件。
      </Typography.Text>
      {runtime.ports.length ? (
        <Collapse
          size="small"
          items={runtime.ports.map((port) => ({
            key: String(port.portNo),
            label: (
              <Space wrap>
                <Typography.Text strong>
                  {port.portNo} 号投口 · {port.displayName}
                </Typography.Text>
                <RuntimeTag value={port.safetyStatus} />
                <RuntimeTag value={port.weightSensorHealth} />
                <RuntimeTag value={port.smokeState} />
              </Space>
            ),
            children: <PortRuntimePanel port={port} />,
          }))}
        />
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="永久分配机构后才会建立投口运行状态"
        />
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
      unitPriceYuanPerKg:
        edits.ports?.[index]?.unitPriceYuanPerKg
        ?? port.unitPriceYuanPerKg,
      fullnessWeightKg:
        edits.ports?.[index]?.fullnessWeightKg
        ?? port.fullnessWeightKg,
    })),
  };
}

function AcceptanceEvidenceFacts({
  evidence,
}: {
  evidence: DeviceAcceptanceEvidence;
}) {
  const functionalFacts = [
    ['OneNet 在线', evidence.oneNetOnline],
    ['持久化存储', evidence.persistentStoreHealthy],
    ['配置持久化', evidence.configurationPersistenceHealthy],
    ['MCU 通信', evidence.mcuCommunicationHealthy],
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
              {factoryFailureGuidance(code).title}（{code}）
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
        <Descriptions.Item label="可信时间（诊断）">
          <Tag>
            {evidence.trustedTimeHealthy ? '已同步' : '未同步，不影响验收结论'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="MCU 来源（诊断）">
          <Tag>{evidence.mcuSimulated ? '模拟来源' : '真实来源'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="摄像头来源（诊断）">
          <Tag>{evidence.camerasSimulated ? '模拟来源' : '真实来源'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="证据观测时间">
          {formatShanghaiTime(evidence.observedAt)}
        </Descriptions.Item>
        <Descriptions.Item label="后端接收时间">
          {formatShanghaiTime(evidence.receivedAt)}
        </Descriptions.Item>
        <Descriptions.Item label="证据 UID" span={2}>
          <Typography.Text copyable code>{evidence.evidenceUid}</Typography.Text>
        </Descriptions.Item>
      </Descriptions>
    </Space>
  );
}

function EvidencePanel({ rows }: { rows: DeviceAcceptanceEvidence[] }) {
  if (!rows.length) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="当前验收代次尚未保存自动验收证据"
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
            <Typography.Text strong>历史证据 {index + 1}</Typography.Text>
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
        <Descriptions size="small" column={1} colon={false}>
          <Descriptions.Item label="当次判定">
            <Tag color={acceptanceColors[evidence.evaluationStatus]}>
              {acceptanceLabels[evidence.evaluationStatus]}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="证据 UID">
            <Typography.Text copyable code>{evidence.evidenceUid}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="证据摘要">
            <Typography.Text
              copyable={{ text: evidence.evidenceSha256 }}
              code
              style={{ overflowWrap: 'anywhere' }}
            >
              {evidence.evidenceSha256}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="后端收到">
            {formatShanghaiTime(evidence.receivedAt)}
          </Descriptions.Item>
        </Descriptions>
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
      <Descriptions.Item label="任务 UID">
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
              ? ` · ${task.latestAttempt.technicalResult}`
              : ''}
          </Descriptions.Item>
          <Descriptions.Item label="外部请求编号">
            {task.latestAttempt.externalRequestId
              ? (
                <Typography.Text copyable>
                  {task.latestAttempt.externalRequestId}
                </Typography.Text>
              )
              : '未记录'}
          </Descriptions.Item>
          <Descriptions.Item label="HTTP / 外部错误">
            {task.latestAttempt.httpStatus ?? '-'} / {task.latestAttempt.externalErrorCode ?? '-'}
          </Descriptions.Item>
          <Descriptions.Item label="结果落库">
            {optionalTime(task.latestAttempt.recordedAt)}
          </Descriptions.Item>
        </>
      )}
      {(task.blockedDiagnostic || task.latestAttempt?.diagnostic) && (
        <Descriptions.Item label="脱敏诊断" span={2}>
          <Typography.Text code style={{ overflowWrap: 'anywhere' }}>
            {task.blockedDiagnostic ?? task.latestAttempt?.diagnostic}
          </Typography.Text>
        </Descriptions.Item>
      )}
    </Descriptions>
  );
}

function FactoryProgressPanel({
  load,
  onRefresh,
}: {
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

  return (
    <section aria-labelledby="factory-progress-title">
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
            {progress.seal.status !== 'SEALED' && (
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
        <Alert
          showIcon
          type={summary.type}
          message={summary.message}
          description={summary.description}
          style={{ marginBottom: 12 }}
        />

        {issueCodes.map((code) => {
          const guidance = factoryFailureGuidance(code);
          return (
            <Alert
              key={code}
              type="error"
              showIcon
              style={{ marginBottom: 8 }}
              message={guidance.title}
              description={(
                <Space direction="vertical" size={4}>
                  <Typography.Text>{guidance.action}</Typography.Text>
                  <Typography.Text type="secondary" code>
                    稳定问题代码：{code}
                  </Typography.Text>
                </Space>
              )}
            />
          );
        })}

        <Descriptions size="small" column={2} bordered style={{ marginTop: 12 }}>
          <Descriptions.Item label="初始袋码">
            {progress.factoryBags.verifiedCount}/{progress.factoryBags.expectedPortCount}
            {progress.factoryBags.complete
              ? <Tag color="success" style={{ marginLeft: 8 }}>已完整</Tag>
              : <Tag color="warning" style={{ marginLeft: 8 }}>待补齐</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="袋码修订号">
            {progress.factoryBags.revision}
          </Descriptions.Item>
          <Descriptions.Item label="权威验收">
            <Tag color={acceptanceColors[progress.acceptance.status]}>
              {acceptanceLabels[progress.acceptance.status]}
            </Tag>
            <Typography.Text type="secondary">
              第 {progress.acceptance.generation} 代
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
          <Descriptions.Item label="下一步" span={2}>
            {progress.nextActionCodes.length ? (
              <Space wrap>
                {progress.nextActionCodes.map((action) => (
                  <Tag key={action}>{factoryActionLabel(action)}</Tag>
                ))}
              </Space>
            ) : '等待系统继续推进'}
          </Descriptions.Item>
        </Descriptions>

        {evidenceChanged && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 12 }}
            message="最新历史证据不是当前权威结论绑定的证据"
            description="下方两份证据必须分别核对；后来收到的历史证据不会自动改写已经形成的权威 PASSED 和封存绑定。"
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
            title="权威结论绑定证据"
            evidence={progress.acceptance.authoritativeEvidence}
            emptyText="当前尚未形成可绑定的权威证据"
          />
          <FactoryEvidenceReference
            title="最新收到的历史证据"
            evidence={progress.acceptance.latestEvidence}
            emptyText="后端尚未收到设备验收证据"
          />
        </div>

        <Collapse
          size="small"
          style={{ marginTop: 12 }}
          items={[{
            key: 'factory-task-diagnostics',
            label: '可靠任务与时间诊断',
            children: (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <FactoryTaskDiagnostics
                  title="P8 请求"
                  task={progress.acceptanceRequest}
                />
                <FactoryTaskDiagnostics
                  title="封存授权"
                  task={progress.seal}
                />
                <Descriptions size="small" column={2} bordered>
                  <Descriptions.Item label="授权代次">
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
                  <Descriptions.Item label="后端收到完成事件">
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
    </section>
  );
}

export default function DeviceAssetDrawer({
  open,
  mode,
  asset,
  organizationCode,
  onClose,
  onAssignTenant,
  onAssignOrganization,
  onControl,
  onReevaluateAcceptance,
  onChanged,
}: DeviceAssetDrawerProps) {
  const executeCommand = useCommandExecutor();
  const [configForm] = Form.useForm<DailyConfigurationEdits>();
  const [recoveryForm] = Form.useForm<PlatformConfigurationRecovery>();
  const [baselineForm] = Form.useForm<ManualBaselineAttempt>();
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
  const [configurationModalOpen, setConfigurationModalOpen] = useState(false);
  const [recoveryKind, setRecoveryKind] = useState<PlatformRecoveryKind>();
  const [submitting, setSubmitting] = useState(false);
  const [recoverySubmitting, setRecoverySubmitting] = useState(false);
  const [baselineSubmitting, setBaselineSubmitting] = useState(false);
  const [baselineIssue, setBaselineIssue] = useState<DeviceTechnicalIssue>();
  const [reevaluating, setReevaluating] = useState(false);
  const [entryQrDataUrl, setEntryQrDataUrl] = useState<string>();
  const [entryQrError, setEntryQrError] = useState(false);
  const technicalIssues = technicalIssueLoad.data;
  const loadingTechnicalIssues = technicalIssueLoad.status === 'loading';
  const loadingRuntime = runtimeLoad.status === 'loading';
  const hardwareSn = asset?.hardwareSn;

  const canConfigure = Boolean(asset) && (
    (mode === 'organization' && Boolean(organizationCode))
    || (mode === 'platform' && Boolean(asset?.organizationCode))
  );

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
        setLatestVersion(version);
        if (mode === 'platform') {
          setLatestApplication(
            await getPlatformDeviceConfigurationApplication(
              asset.hardwareSn,
              latest.application.applicationUid,
            ),
          );
        } else {
          setLatestApplication(undefined);
        }
      } else {
        setLatestVersion(undefined);
        setLatestApplication(undefined);
      }
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setLoadingConfiguration(false);
    }
  };

  useEffect(() => {
    runtimeRequest.current += 1;
    factoryProgressRequest.current += 1;
    factoryProgressInFlight.current = null;
    factoryProgressRefreshQueued.current = false;
    factoryProgressSealed.current = false;
    technicalIssueRequest.current += 1;
    if (!open) return;
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
      if (timer == null && !factoryProgressSealed.current) void poll();
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
  }, [hardwareSn, loadFactoryProgress, mode, open]);

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
    if (!open || !asset?.deviceEntryUrl) return () => undefined;
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
  }, [open, asset?.deviceEntryUrl]);

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
          {!asset.tenantCode && (
            <Button type="primary" onClick={() => onAssignTenant(asset)}>
              永久分配租户
            </Button>
          )}
          <Button
            icon={<ReloadOutlined />}
            loading={reevaluating}
            onClick={async () => {
              setReevaluating(true);
              try {
                await onReevaluateAcceptance(asset);
                await Promise.all([
                  loadEvidence(),
                  loadTechnicalIssues(),
                  loadFactoryProgress(),
                ]);
              } finally {
                setReevaluating(false);
              }
            }}
          >
            重新读取验收证据
          </Button>
          {asset.lifecycleStatus === 'NORMAL' && (
            <Button onClick={() => onControl(asset, 'disable')}>禁用</Button>
          )}
          {asset.lifecycleStatus === 'DISABLED' && (
            <Button type="primary" onClick={() => onControl(asset, 'restore')}>
              恢复
            </Button>
          )}
          {asset.lifecycleStatus !== 'RETIRED' && (
            <Button danger onClick={() => onControl(asset, 'retire')}>
              报废
            </Button>
          )}
        </Space>
      );
    }
    if (mode === 'tenant' && !asset.organizationCode) {
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
        unitPriceYuanPerKg: port.unitPriceYuanPerKg,
        fullnessWeightKg: port.fullnessWeightKg,
      })),
    });
    setConfigurationModalOpen(true);
  };

  const publishConfiguration = async () => {
    if (!asset || !organizationCode || !latestVersion) return;
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
        `已为 ${baselineIssue.portNo} 号投口创建新的皮重测量代际`,
      );
      setBaselineIssue(undefined);
      await loadTechnicalIssues();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setBaselineSubmitting(false);
    }
  };

  const reevaluateAcceptanceFromIssue = async () => {
    if (!asset) return;
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
      {issue.nextActions.includes('REEVALUATE_ACCEPTANCE') && (
        <Button
          size="small"
          loading={reevaluating}
          onClick={() => void reevaluateAcceptanceFromIssue()}
        >
          重新读取验收证据
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
              PERMANENT DEVICE ASSET
            </Typography.Text>
            <Typography.Text strong>{asset?.hardwareSn ?? '设备详情'}</Typography.Text>
          </Space>
        }
        extra={actionButtons}
      >
        {!asset ? (
          <Empty description="请选择设备" />
        ) : (
          <Space direction="vertical" size={24} style={{ width: '100%' }}>
            <Card
              styles={{ body: { padding: 0 } }}
              style={{ borderLeft: '4px solid #1677ff' }}
            >
              <Descriptions column={2} bordered size="small">
                <Descriptions.Item label="设备公开码" span={2}>
                  <Typography.Text copyable code>{asset.deviceCode}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="硬件 SN">
                  <Typography.Text copyable>{asset.hardwareSn}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="型号">{asset.modelCode}</Descriptions.Item>
                <Descriptions.Item label="机器验收">
                  <Tag color={acceptanceColors[asset.acceptanceStatus]}>
                    {acceptanceLabels[asset.acceptanceStatus]}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="生命周期">
                  <Tag color={assetColors[asset.lifecycleStatus]}>
                    {assetLabels[asset.lifecycleStatus]}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="设备联网">
                  <Space direction="vertical" size={0}>
                    <Tag color={connectivityColors[
                      asset.connectivity?.oneNetConnectionStatus ?? 'UNKNOWN'
                    ]}>
                      {connectivityLabels[
                        asset.connectivity?.oneNetConnectionStatus ?? 'UNKNOWN'
                      ]}
                    </Tag>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {optionalTime(asset.connectivity?.statusObservedAt)}
                    </Typography.Text>
                  </Space>
                </Descriptions.Item>
                <Descriptions.Item label="永久租户">
                  {asset.tenantCode ?? '尚未分配'}
                </Descriptions.Item>
                <Descriptions.Item label="永久机构">
                  {asset.organizationCode ?? '尚未分配'}
                </Descriptions.Item>
                <Descriptions.Item label="现场设备名称">
                  {asset.installationProfile.displayName}
                </Descriptions.Item>
                <Descriptions.Item label="安装资料版本">
                  V{asset.installationProfile.version}
                  {asset.installationProfile.complete ? (
                    <Tag color="success" style={{ marginLeft: 8 }}>完整</Tag>
                  ) : (
                    <Tag color="warning" style={{ marginLeft: 8 }}>待完善</Tag>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="安装地址" span={2}>
                  {asset.installationProfile.address ?? '尚未设置'}
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
                <Descriptions.Item label="投口数量">
                  {asset.expectedPortCount}
                </Descriptions.Item>
                <Descriptions.Item label="小程序入口" span={2}>
                  {asset.deviceEntryUrl ? (
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
                </Descriptions.Item>
                <Descriptions.Item label="OneNet 设备名" span={2}>
                  {asset.oneNetMapping.deviceName}
                </Descriptions.Item>
              </Descriptions>
            </Card>

            {mode === 'platform' && (
              <FactoryProgressPanel
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
                  marginBottom: 12,
                }}
              >
                <Space>
                  <MonitorOutlined />
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    当前联网与最近运行状态
                  </Typography.Title>
                  {runtimeLoad.data && (
                    <RuntimeTag
                      value={runtimeLoad.data.health.oneNetConnectionStatus}
                    />
                  )}
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    每 15 秒自动刷新
                  </Typography.Text>
                </Space>
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  loading={loadingRuntime}
                  onClick={() => void loadRuntime()}
                >
                  立即刷新
                </Button>
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
                  <RuntimeStatusPanel runtime={runtimeLoad.data} />
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

            {mode === 'platform' && (
              <RemoteSupportPanel hardwareSn={asset.hardwareSn} />
            )}

            {mode === 'platform' && (
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
                  {technicalIssueLoad.status === 'loaded'
                    && !technicalIssues.length ? (
                    <Alert
                      type="success"
                      showIcon
                      message="当前没有需要平台处理的设备问题"
                      description="系统仍会继续监测验收、配置、投递、清运和初始空袋皮重。"
                    />
                    ) : null}
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
                                  {issue.title}
                                </Typography.Text>
                                <Tag>{issue.state}</Tag>
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
                                  {issue.description}
                                </Typography.Text>
                                <Typography.Text
                                  type="secondary"
                                  style={{ fontSize: 12 }}
                                >
                                  问题代码：{issue.code}
                                  {issue.automaticAttemptNo != null
                                    && issue.automaticAttemptLimit != null
                                    ? ` · 系统测量 ${issue.automaticAttemptNo}/${issue.automaticAttemptLimit}`
                                    : ''}
                                  {issue.occurredAt
                                    ? ` · ${formatShanghaiTime(issue.occurredAt)}`
                                    : ''}
                                </Typography.Text>
                                {issue.diagnostic && (
                                  <Typography.Text
                                    type="secondary"
                                    code
                                    style={{ fontSize: 12 }}
                                  >
                                    {issue.diagnostic}
                                  </Typography.Text>
                                )}
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

            {mode === 'platform' && (
              <section>
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
                          出厂机器验收证据（历史审计）
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
                          message="这里的每一行都是历史证据，不代表当前权威验收状态"
                          description="请以“接入与封存进度”中的权威结论绑定证据为准；最新收到的证据和历史失败都不会自行替代权威绑定关系。可信时间和模拟来源只作中性诊断。"
                        />
                        <Spin spinning={loadingEvidence}>
                          <EvidencePanel rows={evidence} />
                        </Spin>
                      </Space>
                    ),
                  }]}
                />
              </section>
            )}

            {mode === 'platform' && !asset.organizationCode && (
              <Alert
                type="info"
                showIcon
                message="永久分配机构后才会生成设备配置"
                description="设备配置包含机构价格和投口经营参数；分配完成后系统自动创建初始版本，机构只需安装、通电和联网。"
              />
            )}

            {canConfigure && (
              <section>
                <Space
                  align="center"
                  style={{ width: '100%', justifyContent: 'space-between' }}
                >
                  <Space>
                    <CloudSyncOutlined />
                    <Typography.Title level={5} style={{ margin: 0 }}>
                      {mode === 'platform'
                        ? '配置下发与恢复'
                        : '日常价格与设备配置'}
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
                  ) : (
                    <Button
                      icon={<EditOutlined />}
                      disabled={!latestVersion}
                      onClick={openConfigurationEditor}
                    >
                      基于最新版发布
                    </Button>
                  )}
                </Space>
                <Alert
                  style={{ margin: '12px 0' }}
                  type={mode === 'platform' ? 'warning' : 'info'}
                  showIcon
                  message={mode === 'platform'
                    ? '两种恢复操作处理的问题不同'
                    : '安装、通电和联网后无需机构确认'}
                      description={mode === 'platform'
                        ? '“重新下发”只适用于命令尚未到达设备的传输阻断；同版本但摘要不同属于内容冲突，设备已接收或已经明确失败时，排除故障后都必须发布更高的修复版本。'
                        : '系统会自动下发配置并测量厂家初始袋皮重；这里仅用于日常改价或调整投口配置。'}
                />
                {mode === 'platform' && latestApplication?.lastFailureCode && (
                  <Alert
                    style={{ marginBottom: 12 }}
                    type="error"
                    showIcon
                    message={`最近失败：${latestApplication.lastFailureCode}`}
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
                                <Tag>{version.application.dispatchState}</Tag>
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
              </section>
            )}
          </Space>
        )}
      </Drawer>

      <Modal
        width={760}
        title="发布日常价格与机器配置"
        open={configurationModalOpen}
        confirmLoading={submitting}
        onOk={() => void publishConfiguration()}
        onCancel={() => setConfigurationModalOpen(false)}
        okText="发布并自动下发"
      >
        <Alert
          type="warning"
          showIcon
          message="发布会生成一个不可修改的新版本"
          description="当前进行中的投递或清运继续使用启动时冻结的旧配置；新业务只在新版本精确应用后使用它。设备名称和安装位置由清运员在安装资料页维护，不属于机器配置。"
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
                <Form.Item
                  name={['ports', index, 'unitPriceYuanPerKg']}
                  label="单价（元/千克）"
                  rules={[{ required: true }]}
                >
                  <InputNumber
                    stringMode
                    min="0.0001"
                    precision={4}
                    style={{ width: 180 }}
                  />
                </Form.Item>
                <Form.Item
                  name={['ports', index, 'fullnessWeightKg']}
                  label="满载重量（千克）"
                  rules={[{ required: true }]}
                >
                  <InputNumber
                    stringMode
                    min="0.001"
                    precision={3}
                    style={{ width: 180 }}
                  />
                </Form.Item>
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
            ? '完整复制当前配置，只递增版本身份'
            : '不会产生新版本'}
          description={recoveryKind === 'roll-forward'
            ? '用于当前应用已经失败，或命令送达后证据超时的情况。机构价格、投口和传感器参数不会被平台重新填写或修改。'
            : '仅用于可靠任务明确证明命令尚未到达设备的情况；不会重新执行已经被设备接收的命令。'}
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
        title={`${baselineIssue?.portNo ?? '-'} 号投口：重新测量空袋皮重`}
        open={Boolean(baselineIssue)}
        confirmLoading={baselineSubmitting}
        onOk={() => void submitManualBaselineAttempt()}
        onCancel={() => setBaselineIssue(undefined)}
        okText="创建新的测量代际"
        destroyOnClose
      >
        <Alert
          type="warning"
          showIcon
          message="这不是重放上一条失败命令"
          description="系统会结束保留上一代失败事实，并创建全新的皮重测量和设备命令。只有现场已排除故障且厂家袋仍为空时才能执行。"
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
