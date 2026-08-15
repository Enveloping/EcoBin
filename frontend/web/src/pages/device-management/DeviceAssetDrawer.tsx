import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
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
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CloudSyncOutlined,
  DownloadOutlined,
  EditOutlined,
  QrcodeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import QRCode from 'qrcode';
import {
  getDeviceConfigurationVersion,
  getPlatformDeviceConfigurationApplication,
  getPlatformDeviceConfigurationVersion,
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
} from './devicePresentation';
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

const EMPTY_TECHNICAL_ISSUE_LOAD: TechnicalIssueLoadState = {
  status: 'idle',
  data: [],
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

function EvidencePanel({ rows }: { rows: DeviceAcceptanceEvidence[] }) {
  if (!rows.length) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="设备联网后会自动提交功能验收证据"
      />
    );
  }
  const latest = rows[0];
  const facts = [
    ['OneNet 在线', latest.oneNetOnline],
    ['持久化存储', latest.persistentStoreHealthy],
    ['可信时间', latest.trustedTimeHealthy],
    ['配置持久化', latest.configurationPersistenceHealthy],
    ['MCU 通信', latest.mcuCommunicationHealthy],
    ['传感器数据', latest.sensorsHealthy],
    ['摄像头采集', latest.camerasCaptureHealthy],
    ['测试图片上传', latest.cameraUploadHealthy],
  ] as const;
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        showIcon
        type={latest.evaluationStatus === 'PASSED' ? 'success' : 'warning'}
        message={
          latest.evaluationStatus === 'PASSED'
            ? '最新功能证据已通过机器验收'
            : '最新证据尚未满足机器验收'
        }
        description={
          latest.failureReasons.length
            ? latest.failureReasons.join('、')
            : `设备软件 ${latest.edgeSoftwareVersion} · 协议 ${latest.edgeProtocolVersion}`
        }
      />
      <Descriptions size="small" column={2} bordered>
        {facts.map(([label, value]) => (
          <Descriptions.Item key={label} label={label}>
            <Tag color={value ? 'success' : 'error'}>
              {booleanEvidence(value)}
            </Tag>
          </Descriptions.Item>
        ))}
        <Descriptions.Item label="MCU 来源">
          <Tag color={latest.mcuSimulated ? 'default' : 'success'}>
            {latest.mcuSimulated ? '模拟器（仅诊断）' : '真实硬件'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="摄像头来源">
          <Tag color={latest.camerasSimulated ? 'default' : 'success'}>
            {latest.camerasSimulated ? '模拟器（仅诊断）' : '真实摄像头'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="观测时间" span={2}>
          {formatShanghaiTime(latest.observedAt)}
        </Descriptions.Item>
      </Descriptions>
      {rows.length > 1 && (
        <Typography.Text type="secondary">
          共保存 {rows.length} 次验收证据；历史失败不会因后来通过而被删除。
        </Typography.Text>
      )}
    </Space>
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
  const [technicalIssueLoad, setTechnicalIssueLoad] =
    useState<TechnicalIssueLoadState>(EMPTY_TECHNICAL_ISSUE_LOAD);
  const [versions, setVersions] = useState<DeviceConfigurationVersionSummary[]>([]);
  const [latestVersion, setLatestVersion] = useState<DeviceConfigurationVersion>();
  const [latestApplication, setLatestApplication] =
    useState<DeviceConfigurationApplication>();
  const [loadingEvidence, setLoadingEvidence] = useState(false);
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

  const canConfigure = Boolean(asset) && (
    (mode === 'organization' && Boolean(organizationCode))
    || (mode === 'platform' && Boolean(asset?.organizationCode))
  );

  const loadEvidence = async () => {
    if (!asset || mode !== 'platform') return;
    setLoadingEvidence(true);
    try {
      setEvidence(await listDeviceAcceptanceEvidence(asset.hardwareSn));
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setLoadingEvidence(false);
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
    technicalIssueRequest.current += 1;
    if (!open) return;
    setEvidence([]);
    setTechnicalIssueLoad(EMPTY_TECHNICAL_ISSUE_LOAD);
    setVersions([]);
    setLatestVersion(undefined);
    setLatestApplication(undefined);
    setBaselineIssue(undefined);
    void loadEvidence();
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
                await Promise.all([loadEvidence(), loadTechnicalIssues()]);
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
  }, [asset, mode, onAssignOrganization, onAssignTenant, onControl, reevaluating]);

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
      await Promise.all([loadEvidence(), loadTechnicalIssues()]);
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
                <Space style={{ marginBottom: 12 }}>
                  <SafetyCertificateOutlined />
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    自动机器验收证据
                  </Typography.Title>
                </Space>
                <Spin spinning={loadingEvidence}>
                  <EvidencePanel rows={evidence} />
                </Spin>
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
