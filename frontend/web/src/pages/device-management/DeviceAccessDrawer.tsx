import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Result,
  Space,
  Spin,
  Steps,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  CloudSyncOutlined,
  PoweroffOutlined,
  ReloadOutlined,
  SendOutlined,
} from '@ant-design/icons';
import {
  acceptPlatformDeviceDeployment,
  getDeviceConfigurationApplication,
  getDeviceConfigurationVersion,
  getDeviceDeployment,
  getDeviceDeploymentRuntime,
  getDevicePortRuntime,
  getPlatformDeviceAcceptanceReadiness,
  listDeviceConfigurationVersions,
  listDeviceDeploymentPorts,
  listPlatformDeviceDeploymentAcceptances,
  listTenantDeviceAssetAllocations,
  releaseDeviceConfiguration,
  resynchronizeDeviceConfiguration,
  returnDeviceDeploymentToTenantPool,
  setDeviceBusinessEnabled,
  suspendPlatformDeviceDeploymentTechnically,
  type DeviceAcceptanceReadiness,
  type DeviceConfigurationApplication,
  type DeviceConfigurationApplicationStatus,
  type DeviceConfigurationReleaseRequest,
  type DeviceConfigurationVersion,
  type DeviceConfigurationVersionSummary,
  type DeviceDeployment,
  type DeviceDeploymentAcceptance,
  type DeviceDeploymentRuntime,
  type DevicePort,
  type DevicePortRuntime,
  type DeviceTenantAllocation,
} from '@/api/deviceDirectory';
import type { DirectoryContext } from '@/api/identityDirectory';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { useAuthStore } from '@/stores/authStore';
import { formatShanghaiTime } from '@/utils/decimal';
import DeviceConfigurationModal from './DeviceConfigurationModal';
import {
  blockerLabel,
  configurationColors,
  configurationLabels,
  connectionStatusColor,
  connectionStatusLabel,
  dispatchLabels,
  healthColor,
  lifecycleColors,
  lifecycleLabels,
} from './devicePresentation';

type CommandKind = 'enable' | 'disable' | 'return' | 'suspend';

interface DeviceAccessDrawerProps {
  open: boolean;
  context: DirectoryContext | null;
  organizationCode?: string;
  deploymentCode?: string;
  onClose: () => void;
  onUpdated: () => void;
  onReturnedToPool: (allocation: DeviceTenantAllocation) => void;
}

interface CommandFormValues {
  reason?: string;
}

interface AcceptanceFormValues {
  deliveryDoorObservedNormal?: boolean;
  camerasObservedNormal?: boolean;
  cleanDoorInstallationObservedNormal?: boolean;
  reason?: string;
}

const commandTitles: Record<CommandKind, string> = {
  enable: '开启经营',
  disable: '关闭经营',
  return: '退回租户设备池',
  suspend: '技术停用部署',
};

const readinessModeLabels: Record<string, string> = {
  PLATFORM_ACCEPTANCE_REQUIRED: '需平台首次验收',
  AUTOMATIC_TRANSFER_READINESS: '同租户调拨自动技术就绪',
  LEGACY_DIRECT_DEPLOYMENT: '历史直接部署',
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '设备详情加载失败';
}

function problemBlockers(error: unknown): string[] {
  if (!(error instanceof ApiProblem)) return [];
  const blockers = error.details.blockers;
  return Array.isArray(blockers)
    ? blockers.filter((value): value is string => typeof value === 'string')
    : [];
}

function dash(value: unknown): string {
  return value === null || value === undefined || value === ''
    ? '—'
    : String(value);
}

function applicationTag(application?: DeviceConfigurationApplication | null) {
  if (!application) return <Tag>尚无配置应用</Tag>;
  return (
    <Tag color={configurationColors[application.status]}>
      {configurationLabels[application.status]}
    </Tag>
  );
}

function connectivitySummary(runtime: DeviceDeploymentRuntime) {
  const oneNet = runtime.health.oneNetConnectionStatus;
  const business = runtime.health.edgeConnectionStatus;
  if (oneNet === 'ONLINE' && business === 'ONLINE') {
    return {
      type: 'success' as const,
      message: 'OneNet 传输与可信运行均在线',
      description:
        'OneNet 已观察到设备连接，且最新可信运行快照仍在后端心跳窗口内。业务命令仍会在执行时复核配置、安全和占位事实。',
    };
  }
  if (oneNet === 'ONLINE') {
    return {
      type: 'warning' as const,
      message: 'OneNet 已连接，但业务运行事实不可用',
      description:
        '设备可以被 OneNet 观察到，但可信运行快照尚未收到或已经过期。后端会阻止普通业务命令，不能把传输在线当作设备可作业。',
    };
  }
  if (oneNet === 'OFFLINE') {
    return {
      type: 'warning' as const,
      message: 'OneNet 传输离线，业务命令等待设备上线',
      description:
        '可靠任务保持等待，不用服务调用轮询设备。后端收到 OneNet 上线通知或可信设备消息后会重新唤醒任务。',
    };
  }
  return {
    type: 'info' as const,
    message: 'OneNet 传输状态尚未确认',
    description:
      '首次配置等接入探测可以按后端规则继续；普通业务命令需要明确在线并取得新鲜可信运行快照。',
  };
}

function stageIndex(
  deployment: DeviceDeployment,
  application?: DeviceConfigurationApplication | null,
): number {
  if (deployment.businessEnabled) return 4;
  if (deployment.lifecycleStatus === 'ENABLED') return 3;
  if (application?.status === 'APPLIED') return 2;
  if (deployment.latestConfigurationVersion !== null) return 1;
  return 0;
}

export default function DeviceAccessDrawer({
  open,
  context,
  organizationCode,
  deploymentCode,
  onClose,
  onUpdated,
  onReturnedToPool,
}: DeviceAccessDrawerProps) {
  const hasCapability = useAuthStore((state) => state.hasCapability);
  const accountType = useAuthStore((state) => state.session?.accountType);
  const platform = context?.domain === 'platform';
  const tenantPrincipal = accountType === 'TENANT_PRINCIPAL';
  const canConfigure = !!platform
    || hasCapability('device.configuration.manage');
  const canManageBusiness = !platform
    && (tenantPrincipal || hasCapability('device.business.manage'));
  const canReturnToPool = !platform
    && (tenantPrincipal || hasCapability('device.allocation.manage'));
  const executeCommand = useCommandExecutor();
  const [commandForm] = Form.useForm<CommandFormValues>();
  const [acceptanceForm] = Form.useForm<AcceptanceFormValues>();
  const requestSequence = useRef(0);
  const applicationSequence = useRef(0);
  const portRequestSequence = useRef(0);
  const configurationRequestSequence = useRef(0);
  const selectedTarget = useRef<string>();
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string>();
  const [deployment, setDeployment] = useState<DeviceDeployment>();
  const [runtime, setRuntime] = useState<DeviceDeploymentRuntime>();
  const [ports, setPorts] = useState<DevicePort[]>([]);
  const [versions, setVersions] = useState<DeviceConfigurationVersionSummary[]>([]);
  const [latestConfiguration, setLatestConfiguration] =
    useState<DeviceConfigurationVersion>();
  const [application, setApplication] =
    useState<DeviceConfigurationApplication>();
  const [allocation, setAllocation] = useState<DeviceTenantAllocation>();
  const [readiness, setReadiness] = useState<DeviceAcceptanceReadiness>();
  const [acceptances, setAcceptances] = useState<DeviceDeploymentAcceptance[]>([]);
  const [configurationOpen, setConfigurationOpen] = useState(false);
  const [configurationSubmitting, setConfigurationSubmitting] = useState(false);
  const [commandKind, setCommandKind] = useState<CommandKind>();
  const [commandSubmitting, setCommandSubmitting] = useState(false);
  const [commandError, setCommandError] = useState<string>();
  const [commandBlockers, setCommandBlockers] = useState<string[]>([]);
  const [acceptanceOpen, setAcceptanceOpen] = useState(false);
  const [portRuntime, setPortRuntime] = useState<DevicePortRuntime>();
  const [portRuntimeLoading, setPortRuntimeLoading] = useState(false);
  const [viewingConfiguration, setViewingConfiguration] =
    useState<DeviceConfigurationVersion>();
  const [viewingConfigurationLoading, setViewingConfigurationLoading] =
    useState(false);

  const targetKey = context && organizationCode && deploymentCode
    ? `${context.domain}:${context.tenantCode ?? ''}:${organizationCode}:${deploymentCode}`
    : undefined;
  selectedTarget.current = open ? targetKey : undefined;

  const load = useCallback(async () => {
    if (!open || !context || !organizationCode || !deploymentCode || !targetKey) {
      return;
    }
    const sequence = ++requestSequence.current;
    setLoading(true);
    setLoadError(undefined);
    setDeployment(undefined);
    setRuntime(undefined);
    setPorts([]);
    setVersions([]);
    setLatestConfiguration(undefined);
    setApplication(undefined);
    setAllocation(undefined);
    setReadiness(undefined);
    setAcceptances([]);
    try {
      const [loadedDeployment, loadedRuntime, loadedPorts, versionPage] =
        await Promise.all([
          getDeviceDeployment(context, organizationCode, deploymentCode),
          getDeviceDeploymentRuntime(context, organizationCode, deploymentCode),
          listDeviceDeploymentPorts(context, organizationCode, deploymentCode),
          listDeviceConfigurationVersions(
            context,
            organizationCode,
            deploymentCode,
            { limit: 100 },
          ),
        ]);
      if (
        requestSequence.current !== sequence
        || selectedTarget.current !== targetKey
      ) return;

      const [extra, latestPair] = await Promise.all([
        platform
          ? Promise.all([
              getPlatformDeviceAcceptanceReadiness(
                context,
                organizationCode,
                deploymentCode,
              ),
              listPlatformDeviceDeploymentAcceptances(
                context,
                organizationCode,
                deploymentCode,
              ),
            ])
          : canReturnToPool
            ? listTenantDeviceAssetAllocations({
                page: 1,
                pageSize: 200,
                status: 'ACTIVE',
                hardwareSn: loadedDeployment.asset.hardwareSn,
              })
            : Promise.resolve(undefined),
        versionPage.items[0]
          ? Promise.all([
              getDeviceConfigurationVersion(
                context,
                organizationCode,
                deploymentCode,
                versionPage.items[0].versionNo,
              ),
              getDeviceConfigurationApplication(
                context,
                organizationCode,
                deploymentCode,
                versionPage.items[0].application.applicationUid,
              ),
            ])
          : Promise.resolve(undefined),
      ]);
      if (
        requestSequence.current !== sequence
        || selectedTarget.current !== targetKey
      ) return;

      setDeployment(loadedDeployment);
      setRuntime(loadedRuntime);
      setPorts(loadedPorts);
      setVersions(versionPage.items);
      setLatestConfiguration(latestPair?.[0]);
      setApplication(latestPair?.[1]);
      if (platform && Array.isArray(extra)) {
        setReadiness(extra[0] as DeviceAcceptanceReadiness);
        setAcceptances(extra[1] as DeviceDeploymentAcceptance[]);
        setAllocation(undefined);
      } else if (!platform && extra && 'items' in extra) {
        setAllocation(extra.items[0]);
        setReadiness(undefined);
        setAcceptances([]);
      } else {
        setAllocation(undefined);
        setReadiness(undefined);
        setAcceptances([]);
      }
    } catch (error) {
      if (
        requestSequence.current === sequence
        && selectedTarget.current === targetKey
      ) setLoadError(errorMessage(error));
    } finally {
      if (
        requestSequence.current === sequence
        && selectedTarget.current === targetKey
      ) setLoading(false);
    }
  }, [
    canReturnToPool,
    context,
    deploymentCode,
    open,
    organizationCode,
    platform,
    targetKey,
  ]);

  useEffect(() => {
    if (open) void load();
    return () => {
      requestSequence.current += 1;
      applicationSequence.current += 1;
      portRequestSequence.current += 1;
      configurationRequestSequence.current += 1;
    };
  }, [load, open]);

  useEffect(() => {
    if (
      !open
      || !context
      || !organizationCode
      || !deploymentCode
      || !targetKey
      || !application?.recommendedPollAfterMs
    ) return undefined;
    const applicationUid = application.applicationUid;
    const timer = window.setTimeout(async () => {
      const sequence = ++applicationSequence.current;
      try {
        const loaded = await getDeviceConfigurationApplication(
          context,
          organizationCode,
          deploymentCode,
          applicationUid,
        );
        if (
          applicationSequence.current !== sequence
          || selectedTarget.current !== targetKey
          || loaded.applicationUid !== applicationUid
        ) return;
        setApplication(loaded);
        if (loaded.status === 'APPLIED') void load();
      } catch {
        // Keep the last authoritative proof and let the operator refresh.
      }
    }, application.recommendedPollAfterMs);
    return () => window.clearTimeout(timer);
  }, [
    application,
    context,
    deploymentCode,
    load,
    open,
    organizationCode,
    targetKey,
  ]);

  const handleVersionConflict = async (error: unknown) => {
    if (error instanceof ApiProblem && error.isVersionConflict) {
      setCommandKind(undefined);
      setAcceptanceOpen(false);
      setConfigurationOpen(false);
      commandForm.resetFields();
      acceptanceForm.resetFields();
      resetCommandFeedback();
      message.warning('设备、分配或部署版本已更新，已刷新事实，请重新确认');
      await load();
    }
  };

  const resetCommandFeedback = () => {
    setCommandError(undefined);
    setCommandBlockers([]);
  };

  const submitCommand = async () => {
    if (
      !commandKind
      || !context
      || !organizationCode
      || !deploymentCode
      || !deployment
    ) return;
    const values = await commandForm.validateFields();
    const reason = values.reason?.trim() || null;
    resetCommandFeedback();
    setCommandSubmitting(true);
    try {
      if (commandKind === 'return') {
        if (!allocation) throw new Error('无法读取当前租户分配版本');
        const payload = {
          expectedDeploymentVersion: deployment.version,
          expectedAllocationVersion: allocation.allocationVersion,
          reason: reason || '',
        };
        const returned = await executeCommand(
          commandKey('device.deployment.return-to-pool', deploymentCode, payload),
          (intent) => returnDeviceDeploymentToTenantPool(
            context,
            organizationCode,
            deploymentCode,
            payload,
            intent,
          ),
        );
        message.success('原部署已结束，设备现在位于租户设备池，尚未部署');
        setCommandKind(undefined);
        onUpdated();
        onReturnedToPool(returned);
        return;
      }

      const payload = { expectedVersion: deployment.version, reason };
      if (commandKind === 'suspend') {
        await executeCommand(
          commandKey('device.deployment.technical-suspend', deploymentCode, payload),
          (intent) => suspendPlatformDeviceDeploymentTechnically(
            context,
            organizationCode,
            deploymentCode,
            payload,
            intent,
          ),
        );
      } else {
        await executeCommand(
          commandKey(`device.business.${commandKind}`, deploymentCode, payload),
          (intent) => setDeviceBusinessEnabled(
            context,
            organizationCode,
            deploymentCode,
            commandKind === 'enable',
            payload,
            intent,
          ),
        );
      }
      message.success(`${commandTitles[commandKind]}已完成`);
      setCommandKind(undefined);
      commandForm.resetFields();
      await load();
      onUpdated();
    } catch (error) {
      setCommandError(errorMessage(error));
      setCommandBlockers(problemBlockers(error));
      await handleVersionConflict(error);
    } finally {
      setCommandSubmitting(false);
    }
  };

  const submitAcceptance = async () => {
    if (
      !context
      || !organizationCode
      || !deploymentCode
      || !deployment
      || !readiness?.configuration.latestVersion
    ) return;
    const values = await acceptanceForm.validateFields();
    const payload = {
      expectedDeploymentVersion: deployment.version,
      expectedConfigurationVersion: readiness.configuration.latestVersion,
      deliveryDoorObservedNormal: values.deliveryDoorObservedNormal === true,
      camerasObservedNormal: values.camerasObservedNormal === true,
      cleanDoorInstallationObservedNormal:
        values.cleanDoorInstallationObservedNormal === true,
      reason: values.reason?.trim() || null,
    };
    resetCommandFeedback();
    setCommandSubmitting(true);
    try {
      await executeCommand(
        commandKey('device.deployment.accept', deploymentCode, payload),
        (intent) => acceptPlatformDeviceDeployment(
          context,
          organizationCode,
          deploymentCode,
          payload,
          intent,
        ),
      );
      message.success('平台静态硬件验收已记录，租户可在运行资格满足后开启经营');
      setAcceptanceOpen(false);
      await load();
      onUpdated();
    } catch (error) {
      setCommandError(errorMessage(error));
      setCommandBlockers(problemBlockers(error));
      await handleVersionConflict(error);
    } finally {
      setCommandSubmitting(false);
    }
  };

  const submitConfiguration = async (
    payload: DeviceConfigurationReleaseRequest,
  ) => {
    if (!context || !organizationCode || !deploymentCode) return;
    setConfigurationSubmitting(true);
    try {
      await executeCommand(
        commandKey('device.configuration.release', deploymentCode, payload),
        (intent) => releaseDeviceConfiguration(
          context,
          organizationCode,
          deploymentCode,
          payload,
          intent,
        ),
      );
      message.success('设备配置已可靠受理，正在等待设备应用证明');
      setConfigurationOpen(false);
      await load();
      onUpdated();
    } catch (error) {
      await handleVersionConflict(error);
    } finally {
      setConfigurationSubmitting(false);
    }
  };

  const resynchronize = async () => {
    if (!context || !organizationCode || !deploymentCode || !application) return;
    const payload = {
      expectedVersion: application.version,
      reason: 'Web 人工重新唤醒当前设备配置应用',
    };
    try {
      await executeCommand(
        commandKey(
          'device.configuration.resynchronize',
          application.applicationUid,
          payload,
        ),
        (intent) => resynchronizeDeviceConfiguration(
          context,
          organizationCode,
          deploymentCode,
          application.applicationUid,
          payload,
          intent,
        ),
      );
      message.success('已重新唤醒同一配置应用，不会创建新版本');
      await load();
      onUpdated();
    } catch (error) {
      await handleVersionConflict(error);
    }
  };

  const showPortRuntime = async (portNo: number) => {
    if (!context || !organizationCode || !deploymentCode || !targetKey) return;
    const sequence = ++portRequestSequence.current;
    setPortRuntime(undefined);
    setPortRuntimeLoading(true);
    try {
      const loaded = await getDevicePortRuntime(
        context,
        organizationCode,
        deploymentCode,
        portNo,
      );
      if (
        portRequestSequence.current === sequence
        && selectedTarget.current === targetKey
      ) setPortRuntime(loaded);
    } finally {
      if (portRequestSequence.current === sequence) setPortRuntimeLoading(false);
    }
  };

  const showConfiguration = async (versionNo: number) => {
    if (!context || !organizationCode || !deploymentCode || !targetKey) return;
    const sequence = ++configurationRequestSequence.current;
    setViewingConfiguration(undefined);
    setViewingConfigurationLoading(true);
    try {
      const loaded = await getDeviceConfigurationVersion(
        context,
        organizationCode,
        deploymentCode,
        versionNo,
      );
      if (
        sequence === configurationRequestSequence.current
        && selectedTarget.current === targetKey
      ) setViewingConfiguration(loaded);
    } finally {
      if (sequence === configurationRequestSequence.current) {
        setViewingConfigurationLoading(false);
      }
    }
  };

  const openCommand = (kind: CommandKind) => {
    resetCommandFeedback();
    commandForm.setFieldsValue({ reason: undefined });
    setCommandKind(kind);
  };

  const blockerList = (blockers: string[]) => (
    <ul style={{ margin: 0, paddingInlineStart: 20 }}>
      {blockers.map((blocker) => (
        <li key={blocker}>{blockerLabel(blocker)}</li>
      ))}
    </ul>
  );

  const connectivity = runtime ? connectivitySummary(runtime) : undefined;

  const overview = deployment && runtime ? (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small" title="接入步骤" className="device-access-card">
        <Steps
          size="small"
          current={stageIndex(deployment, application)}
          items={[
            { title: '机构部署', description: '新部署与投口已建立' },
            { title: '发布配置', description: '建立完整不可变快照' },
            { title: '设备应用', description: '取得香橙派可信证明' },
            { title: '技术就绪', description: '首次验收或同租户自动就绪' },
            { title: '开始经营', description: '租户允许后续新作业' },
          ]}
        />
      </Card>

      {connectivity && (
        <Alert
          showIcon
          type={connectivity.type}
          message={connectivity.message}
          description={connectivity.description}
        />
      )}

      {runtime.deliveryBlockers.length || runtime.cleaningBlockers.length ? (
        <Alert
          showIcon
          type="warning"
          message="当前仍有业务阻断条件"
          description={(
            <Space direction="vertical" size={4}>
              <span>
                投递：{runtime.deliveryBlockers.length
                  ? runtime.deliveryBlockers.map(blockerLabel).join('、')
                  : '无'}
              </span>
              <span>
                清运：{runtime.cleaningBlockers.length
                  ? runtime.cleaningBlockers.map(blockerLabel).join('、')
                  : '无'}
              </span>
            </Space>
          )}
        />
      ) : (
        <Alert
          showIcon
          type="success"
          message="当前运行投影没有投递或清运阻断"
          description="真正开始作业时，后端仍会在事务中重新检查安全、配置和占位事实。"
        />
      )}

      {!platform && deployment.lifecycleStatus === 'COMMISSIONING' && (
        <Alert
          showIcon
          type="info"
          message="正在等待技术就绪"
          description="首次接入由平台核对运行证据并完成验收；同租户已验收设备重新部署时，系统在新配置和运行证明完整后自动进入技术就绪。"
        />
      )}
      {!platform && deployment.lifecycleStatus === 'ENABLED' && (
        <Alert
          showIcon
          type="success"
          message="该部署已技术就绪"
          description="经营开关仍由租户单独决定，技术就绪不会自动开启经营。"
        />
      )}
      {canReturnToPool && deployment.businessEnabled && (
        <Alert
          showIcon
          type="warning"
          message="调拨前必须先关闭经营"
          description="关闭经营只阻止后续新作业；已开始的投递或清运必须继续收敛，完成后才能退回租户池。"
        />
      )}

      <Card size="small" title="部署与经营状态">
        <Descriptions size="small" column={3}>
          <Descriptions.Item label="部署状态">
            <Tag color={lifecycleColors[deployment.lifecycleStatus]}>
              {lifecycleLabels[deployment.lifecycleStatus]}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="经营开关">
            <Badge
              status={deployment.businessEnabled ? 'success' : 'default'}
              text={deployment.businessEnabled ? '已开启' : '已关闭'}
            />
          </Descriptions.Item>
          <Descriptions.Item label="整机占位">
            {runtime.occupied ? <Tag color="warning">作业中</Tag> : <Tag>空闲</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="租户">
            {deployment.tenantCode}
          </Descriptions.Item>
          <Descriptions.Item label="机构">
            {deployment.organizationCode}
          </Descriptions.Item>
          <Descriptions.Item label="硬件序列号">
            <Typography.Text copyable>{deployment.asset.hardwareSn}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="硬件型号">
            {deployment.asset.modelCode}
          </Descriptions.Item>
          <Descriptions.Item label="投口数量">
            {deployment.portCount}
          </Descriptions.Item>
          <Descriptions.Item label="部署版本">
            v{deployment.version}
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </Space>
  ) : null;

  const runtimePanel = deployment && runtime ? (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small" title="连接与可信运行">
        <Alert
          showIcon
          type="info"
          message="两层在线事实不能互相替代"
          description="OneNet 传输在线只说明云平台能观察到设备连接；业务有效在线还要求后端收到未过期的可信运行快照。"
          style={{ marginBottom: 16 }}
        />
        <Descriptions size="small" column={2}>
          <Descriptions.Item label="OneNet 传输状态">
            <Tag color={connectionStatusColor(runtime.health.oneNetConnectionStatus)}>
              {connectionStatusLabel(runtime.health.oneNetConnectionStatus)}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="OneNet 状态观测时间">
            {runtime.health.oneNetStatusObservedAt
              ? formatShanghaiTime(runtime.health.oneNetStatusObservedAt)
              : '尚未观测'}
          </Descriptions.Item>
          <Descriptions.Item label="业务有效在线">
            <Tag color={connectionStatusColor(runtime.health.edgeConnectionStatus)}>
              {connectionStatusLabel(runtime.health.edgeConnectionStatus)}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="可信运行事实时间">
            {runtime.health.trustedRuntimeReceivedAt
              ? formatShanghaiTime(runtime.health.trustedRuntimeReceivedAt)
              : '尚未收到'}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Card size="small" title="整机运行事实">
        <Descriptions size="small" column={3}>
          {[
            ['MCU 链路', runtime.health.mcuLinkStatus],
            ['整机安全', runtime.health.safetyStatus],
            ['称重健康', runtime.health.aggregateWeightHealth],
            ['相机健康', runtime.health.cameraHealth],
            ['本地存储', runtime.health.localStorageHealth],
            ['时钟同步', runtime.health.clockSyncHealth],
            ['UART 状态', runtime.health.uartState],
          ].map(([label, value]) => (
            <Descriptions.Item label={label} key={label}>
              <Tag color={healthColor(value)}>{dash(value)}</Tag>
            </Descriptions.Item>
          ))}
          <Descriptions.Item label="投递资格">
            <Tag color={runtime.deliveryAllowed ? 'success' : 'default'}>
              {runtime.deliveryAllowed ? '允许' : '阻断'}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="清运资格">
            <Tag color={runtime.cleaningAllowed ? 'success' : 'default'}>
              {runtime.cleaningAllowed ? '允许' : '阻断'}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="边缘程序">
            {dash(runtime.health.edgeSoftwareVersion)}
          </Descriptions.Item>
          <Descriptions.Item label="MCU 固件">
            {dash(runtime.health.mcuFirmwareVersion)}
          </Descriptions.Item>
          <Descriptions.Item label="UART 协议">
            {runtime.health.uartProtocolMajor === null
              ? '—'
              : `${runtime.health.uartProtocolMajor}.${runtime.health.uartProtocolMinor ?? 0}`}
          </Descriptions.Item>
          <Descriptions.Item label="最后心跳">
            {runtime.health.lastHeartbeatAt
              ? formatShanghaiTime(runtime.health.lastHeartbeatAt)
              : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="最后设备事件">
            {runtime.health.lastDeviceEventAt
              ? formatShanghaiTime(runtime.health.lastDeviceEventAt)
              : '—'}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Card size="small" title="投口运行与业务资格">
        <Table<DevicePort>
          size="small"
          rowKey="portNo"
          pagination={false}
          dataSource={ports}
          scroll={{ x: 860 }}
          columns={[
            { title: '投口', dataIndex: 'portNo', width: 80 },
            { title: '名称', dataIndex: 'displayName', render: (value) => dash(value) },
            {
              title: '投递开关',
              dataIndex: 'enabled',
              width: 110,
              render: (value) => value === null
                ? <Tag>未配置</Tag>
                : <Tag color={value ? 'success' : 'default'}>
                    {value ? '启用' : '停用'}
                  </Tag>,
            },
            {
              title: '单价',
              dataIndex: 'unitPriceYuanPerKg',
              width: 130,
              render: (value) => value === null ? '—' : `¥ ${value}/kg`,
            },
            { title: '满溢判断', dataIndex: 'fullnessMode', width: 150, render: (value) => dash(value) },
            {
              title: '配置版本',
              dataIndex: 'configurationVersion',
              width: 110,
              render: (value) => value === null ? '—' : `v${value}`,
            },
            {
              title: '操作',
              width: 110,
              render: (_, port) => (
                <Button type="link" onClick={() => void showPortRuntime(port.portNo)}>
                  详细运行事实
                </Button>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  ) : null;

  const configurationPanel = (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        showIcon
        type="info"
        message="配置快照、可靠下发和设备证明是三类事实"
        description="任务完成不能替代设备应用证明。当前 fixed-frame 适配中，“已应用”只证明香橙派已可靠保存并启用，不表示 MCU 已真实接收。"
      />
      <Card
        size="small"
        title="当前期望配置"
        extra={canConfigure && (
          <Space>
            {application?.nextActions.includes('RESYNCHRONIZE') && (
              <Popconfirm
                title="重新唤醒同一配置应用？"
                description="这不会创建新版本，也不会伪造设备已经应用。"
                onConfirm={() => void resynchronize()}
              >
                <Button icon={<CloudSyncOutlined />}>重新同步</Button>
              </Popconfirm>
            )}
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={() => setConfigurationOpen(true)}
            >
              {latestConfiguration ? '发布新版本' : '发布首版配置'}
            </Button>
          </Space>
        )}
      >
        {latestConfiguration && application ? (
          <Descriptions size="small" column={3}>
            <Descriptions.Item label="期望版本">v{application.versionNo}</Descriptions.Item>
            <Descriptions.Item label="设备应用">{applicationTag(application)}</Descriptions.Item>
            <Descriptions.Item label="可靠任务">
              <Tag>{dispatchLabels[application.dispatchState] ?? application.dispatchState}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="边缘已保存">
              {application.edgePersistedAt
                ? formatShanghaiTime(application.edgePersistedAt)
                : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="MCU 已同步">
              {application.mcuSyncedAt
                ? formatShanghaiTime(application.mcuSyncedAt)
                : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="应用完成">
              {application.appliedAt
                ? formatShanghaiTime(application.appliedAt)
                : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="最近失败">
              {application.lastFailureCode
                ? blockerLabel(application.lastFailureCode)
                : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="应用版本">v{application.version}</Descriptions.Item>
            <Descriptions.Item label="取代状态">
              {application.superseded ? <Tag>已被新版本取代</Tag> : <Tag color="blue">当前期望</Tag>}
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未发布设备配置" />
        )}
      </Card>
      <Card size="small" title="不可变配置历史">
        <Table<DeviceConfigurationVersionSummary>
          size="small"
          rowKey="versionNo"
          pagination={false}
          dataSource={versions}
          scroll={{ x: 900 }}
          columns={[
            {
              title: '版本',
              dataIndex: 'versionNo',
              width: 100,
              render: (value, row) => (
                <Space>
                  v{value}
                  {row.versionNo === latestConfiguration?.versionNo && <Tag color="blue">当前</Tag>}
                </Space>
              ),
            },
            { title: '设备名称', dataIndex: 'deviceDisplayName' },
            {
              title: '应用状态',
              dataIndex: ['application', 'status'],
              width: 140,
              render: (value: DeviceConfigurationApplicationStatus) => (
                <Tag color={configurationColors[value]}>{configurationLabels[value]}</Tag>
              ),
            },
            {
              title: '下发任务',
              dataIndex: ['application', 'dispatchState'],
              width: 120,
              render: (value) => dispatchLabels[value] ?? value,
            },
            { title: '发布人', dataIndex: 'publishedBy', width: 120 },
            {
              title: '发布时间',
              dataIndex: 'publishedAt',
              width: 180,
              render: (value) => formatShanghaiTime(value),
            },
            {
              title: '操作',
              width: 90,
              render: (_, row) => (
                <Button type="link" onClick={() => void showConfiguration(row.versionNo)}>
                  查看
                </Button>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );

  const acceptancePanel = platform ? (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {readiness ? (
        <>
          <Alert
            showIcon
            type={readiness.ready ? 'success' : 'warning'}
            message={readiness.ready
              ? '当前可信事实已满足技术就绪条件'
              : '当前仍有验收阻断项'}
            description={readiness.blockers.length
              ? blockerList(readiness.blockers)
              : '提交验收时还需分别确认投递门、摄像头和清运门的真实安装状态。'}
          />
          <Card
            size="small"
            title="平台验收就绪证据"
            extra={
              readiness.readinessMode === 'PLATFORM_ACCEPTANCE_REQUIRED'
              && ['PENDING_INSTALL', 'COMMISSIONING', 'DISABLED'].includes(
                deployment?.lifecycleStatus ?? '',
              ) ? (
                <Button
                  type="primary"
                  icon={<CheckCircleOutlined />}
                  disabled={!readiness.ready}
                  onClick={() => {
                    resetCommandFeedback();
                    acceptanceForm.resetFields();
                    setAcceptanceOpen(true);
                  }}
                >
                  提交平台验收
                </Button>
              ) : undefined
            }
          >
            <Descriptions size="small" column={3}>
              <Descriptions.Item label="就绪模式">
                {readinessModeLabels[readiness.readinessMode]
                  ?? readiness.readinessMode}
              </Descriptions.Item>
              <Descriptions.Item label="最新配置">
                {readiness.configuration.latestVersion === null
                  ? '—'
                  : `v${readiness.configuration.latestVersion}`}
              </Descriptions.Item>
              <Descriptions.Item label="精确应用">
                <Tag color={readiness.configuration.preciselyApplied ? 'success' : 'warning'}>
                  {readiness.configuration.preciselyApplied ? '是' : '否'}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="香橙派连接">
                <Tag color={healthColor(readiness.runtime.edgeConnectionStatus)}>
                  {dash(readiness.runtime.edgeConnectionStatus)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="MCU 链路">
                <Tag color={healthColor(readiness.runtime.mcuLinkStatus)}>
                  {dash(readiness.runtime.mcuLinkStatus)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="UART">
                <Tag color={healthColor(readiness.runtime.uartState)}>
                  {dash(readiness.runtime.uartState)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="运行证据时间">
                {readiness.runtime.receivedAt
                  ? formatShanghaiTime(readiness.runtime.receivedAt)
                  : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="待上传事件">
                {dash(readiness.runtime.pendingReliableEventCount)}
              </Descriptions.Item>
              <Descriptions.Item label="投口证据">
                {readiness.ports.length} 个投口
              </Descriptions.Item>
            </Descriptions>
            {readiness.readinessMode === 'AUTOMATIC_TRANSFER_READINESS' && (
              <Alert
                showIcon
                type="info"
                message="同租户内部调拨不重复平台验收"
                description="新部署的配置和运行证明完整后，系统会自动进入技术就绪，不会复制原部署的经营开关。"
                style={{ marginTop: 16 }}
              />
            )}
          </Card>
        </>
      ) : <Spin />}
      <Card size="small" title="不可变验收记录">
        <Table<DeviceDeploymentAcceptance>
          size="small"
          rowKey="acceptanceUid"
          pagination={false}
          dataSource={acceptances}
          locale={{ emptyText: '尚无平台验收记录' }}
          columns={[
            { title: '配置版本', dataIndex: 'configurationVersion', width: 110, render: (value) => `v${value}` },
            { title: '验收人', dataIndex: 'acceptedBy', width: 130 },
            {
              title: '真实确认',
              render: (_, record) => (
                <Space wrap>
                  <Tag color={record.deliveryDoorObservedNormal ? 'success' : 'default'}>投递门</Tag>
                  <Tag color={record.camerasObservedNormal ? 'success' : 'default'}>摄像头</Tag>
                  <Tag color={record.cleanDoorInstallationObservedNormal ? 'success' : 'default'}>清运门</Tag>
                </Space>
              ),
            },
            { title: '原因', dataIndex: 'reason', render: (value) => dash(value) },
            { title: '验收时间', dataIndex: 'acceptedAt', width: 180, render: (value) => formatShanghaiTime(value) },
          ]}
        />
      </Card>
    </Space>
  ) : (
    <Alert
      showIcon
      type={deployment?.lifecycleStatus === 'ENABLED' ? 'success' : 'info'}
      message={deployment?.lifecycleStatus === 'ENABLED'
        ? '当前部署已技术就绪'
        : '当前部署仍在等待技术就绪'}
      description="平台负责首次静态硬件验收；同租户已验收设备重新部署时，系统按新配置与运行证明自动判定。租户不能代替平台写入验收记录。"
    />
  );

  const content = loading && !deployment ? (
    <div style={{ padding: '72px 0', textAlign: 'center' }}>
      <Spin tip="正在读取设备接入事实" />
    </div>
  ) : loadError && !deployment ? (
    <Result
      status="error"
      title="设备接入信息加载失败"
      subTitle={loadError}
      extra={<Button onClick={() => void load()}>重新加载</Button>}
    />
  ) : deployment ? (
    <>
      {loadError && (
        <Alert
          showIcon
          type="error"
          message="部分设备状态刷新失败"
          description={loadError}
          style={{ marginBottom: 16 }}
        />
      )}
      <Tabs items={[
        { key: 'overview', label: '接入概况', children: overview },
        { key: 'runtime', label: '运行事实', children: runtimePanel },
        { key: 'configuration', label: '设备配置', children: configurationPanel },
        { key: 'acceptances', label: '验收记录', children: acceptancePanel },
      ]} />
    </>
  ) : null;

  return (
    <>
      <Drawer
        title={(
          <Space>
            <span>设备接入</span>
            {deployment && (
              <Typography.Text type="secondary" copyable>
                {deployment.deploymentCode}
              </Typography.Text>
            )}
          </Space>
        )}
        open={open}
        width={1040}
        onClose={onClose}
        destroyOnClose
        extra={(
          <Space wrap>
            <Button
              icon={<ReloadOutlined spin={loading} />}
              onClick={() => void load()}
              disabled={loading}
            >
              刷新
            </Button>
            {deployment && platform && deployment.lifecycleStatus === 'ENABLED' && (
              <Button
                danger
                icon={<PoweroffOutlined />}
                onClick={() => openCommand('suspend')}
              >
                技术停用
              </Button>
            )}
            {deployment && canManageBusiness && deployment.lifecycleStatus === 'ENABLED' && (
              <Button
                type={deployment.businessEnabled ? 'default' : 'primary'}
                danger={deployment.businessEnabled}
                onClick={() => openCommand(
                  deployment.businessEnabled ? 'disable' : 'enable',
                )}
              >
                {deployment.businessEnabled ? '关闭经营' : '开启经营'}
              </Button>
            )}
            {deployment && canReturnToPool
              && deployment.lifecycleStatus !== 'ENDED' && (
                <Button
                  disabled={deployment.businessEnabled || !allocation}
                  onClick={() => openCommand('return')}
                >
                  退回租户设备池
                </Button>
              )}
          </Space>
        )}
      >
        {content}
      </Drawer>

      <DeviceConfigurationModal
        open={configurationOpen}
        submitting={configurationSubmitting}
        portCount={deployment?.portCount ?? 1}
        latest={latestConfiguration}
        onCancel={() => setConfigurationOpen(false)}
        onSubmit={submitConfiguration}
      />

      <Modal
        title={commandKind ? commandTitles[commandKind] : ''}
        open={!!commandKind}
        okText="确认提交"
        confirmLoading={commandSubmitting}
        onOk={() => void submitCommand()}
        onCancel={() => !commandSubmitting && setCommandKind(undefined)}
        destroyOnClose
      >
        {commandKind === 'return' && (
          <Alert
            showIcon
            type="warning"
            message="这是调拨的第一步"
            description="成功后原部署立即结束，设备停留在租户设备池。系统不会自动部署到其他机构，也不会回滚这一步。"
            style={{ marginBottom: 16 }}
          />
        )}
        {commandKind === 'suspend' && (
          <Alert
            showIcon
            type="warning"
            message="技术停用会强制关闭经营"
            description="恢复时需要平台重新核对验收事实，之后由租户再次开启经营。"
            style={{ marginBottom: 16 }}
          />
        )}
        {commandError && (
          <Alert
            showIcon
            type="error"
            message={commandError}
            description={commandBlockers.length ? blockerList(commandBlockers) : undefined}
            style={{ marginBottom: 16 }}
          />
        )}
        <Form<CommandFormValues>
          form={commandForm}
          layout="vertical"
          disabled={commandSubmitting}
        >
          <Form.Item
            name="reason"
            label="原因"
            rules={[
              ...(commandKind === 'return'
                ? [{ required: true, message: '请填写调拨原因' }]
                : []),
              { max: 500, message: '最多 500 个字符' },
            ]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="提交平台静态硬件验收"
        open={acceptanceOpen}
        okText="记录验收"
        confirmLoading={commandSubmitting}
        onOk={() => void submitAcceptance()}
        onCancel={() => !commandSubmitting && setAcceptanceOpen(false)}
        destroyOnClose
      >
        <Alert
          showIcon
          type="warning"
          message="只能确认现场真实观察"
          description="网络在线、配置应用或运行数据都不能代替门体和摄像头的现场检查。"
          style={{ marginBottom: 16 }}
        />
        {commandError && (
          <Alert
            showIcon
            type="error"
            message={commandError}
            description={commandBlockers.length ? blockerList(commandBlockers) : undefined}
            style={{ marginBottom: 16 }}
          />
        )}
        <Form<AcceptanceFormValues>
          form={acceptanceForm}
          layout="vertical"
          disabled={commandSubmitting}
        >
          {[
            ['deliveryDoorObservedNormal', '我已现场确认投递门安装与动作正常'],
            ['camerasObservedNormal', '我已现场确认内外摄像头安装正常'],
            ['cleanDoorInstallationObservedNormal', '我已现场确认清运门安装正常'],
          ].map(([name, label]) => (
            <Form.Item
              key={name}
              name={name}
              valuePropName="checked"
              rules={[{
                validator: (_, value) => value
                  ? Promise.resolve()
                  : Promise.reject(new Error('该项尚未确认')),
              }]}
            >
              <Checkbox>{label}</Checkbox>
            </Form.Item>
          ))}
          <Form.Item
            name="reason"
            label="验收说明"
            rules={[{ max: 500, message: '最多 500 个字符' }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={portRuntime ? `${portRuntime.portNo} 号投口运行事实` : '投口运行事实'}
        open={portRuntimeLoading || !!portRuntime}
        footer={null}
        onCancel={() => {
          portRequestSequence.current += 1;
          setPortRuntime(undefined);
          setPortRuntimeLoading(false);
        }}
      >
        {portRuntimeLoading ? (
          <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div>
        ) : portRuntime && (
          <Descriptions size="small" column={2} bordered>
            {Object.entries(portRuntime).map(([key, value]) => (
              <Descriptions.Item key={key} label={key}>
                {Array.isArray(value)
                  ? value.map((item) => blockerLabel(String(item))).join('、') || '无'
                  : dash(value)}
              </Descriptions.Item>
            ))}
          </Descriptions>
        )}
      </Modal>

      <Modal
        title={viewingConfiguration
          ? `设备配置 v${viewingConfiguration.versionNo}`
          : '设备配置'}
        open={viewingConfigurationLoading || !!viewingConfiguration}
        footer={null}
        width={880}
        onCancel={() => {
          configurationRequestSequence.current += 1;
          setViewingConfiguration(undefined);
          setViewingConfigurationLoading(false);
        }}
      >
        {viewingConfigurationLoading ? (
          <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div>
        ) : viewingConfiguration && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="设备名称">
                {viewingConfiguration.device.displayName}
              </Descriptions.Item>
              <Descriptions.Item label="安装地址">
                {dash(viewingConfiguration.device.address)}
              </Descriptions.Item>
              <Descriptions.Item label="内容摘要">
                <Typography.Text copyable ellipsis>
                  {viewingConfiguration.contentSha256}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="MCU 摘要">
                <Typography.Text copyable ellipsis>
                  {viewingConfiguration.mcuPayloadSha256}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="发布人">
                {viewingConfiguration.publishedBy}
              </Descriptions.Item>
              <Descriptions.Item label="发布时间">
                {formatShanghaiTime(viewingConfiguration.publishedAt)}
              </Descriptions.Item>
            </Descriptions>
            <Table
              size="small"
              rowKey="portNo"
              pagination={false}
              dataSource={viewingConfiguration.ports}
              columns={[
                { title: '投口', dataIndex: 'portNo', width: 70 },
                { title: '名称', dataIndex: 'displayName' },
                { title: '状态', dataIndex: 'enabled', render: (value) => value ? '启用' : '停用' },
                { title: '单价', dataIndex: 'unitPriceYuanPerKg', render: (value) => `¥ ${value}/kg` },
                { title: '满溢模式', dataIndex: 'fullnessMode' },
                { title: '重量阈值', dataIndex: 'fullnessWeightKg', render: (value) => `${value} kg` },
              ]}
            />
          </Space>
        )}
      </Modal>
    </>
  );
}
