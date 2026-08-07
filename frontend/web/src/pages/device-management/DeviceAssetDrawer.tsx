import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
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
  EditOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import {
  getDeviceConfigurationVersion,
  listDeviceAcceptanceEvidence,
  listDeviceConfigurationVersions,
  releaseDeviceConfiguration,
  type DeviceAcceptanceEvidence,
  type DeviceAsset,
  type DeviceConfigurationReleaseRequest,
  type DeviceConfigurationVersion,
  type DeviceConfigurationVersionSummary,
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
  device?: {
    displayName?: string;
    address?: string;
  };
  ports?: Array<{
    displayName?: string;
    enabled?: boolean;
    unitPriceYuanPerKg?: string;
    fullnessWeightKg?: string;
  }>;
}

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
    locationCorrectionConfirmed:
      (edits.device?.address ?? null) !== current.device.address,
    device: {
      ...current.device,
      displayName: edits.device?.displayName ?? current.device.displayName,
      address: edits.device?.address?.trim() || null,
    },
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
  const [evidence, setEvidence] = useState<DeviceAcceptanceEvidence[]>([]);
  const [versions, setVersions] = useState<DeviceConfigurationVersionSummary[]>([]);
  const [latestVersion, setLatestVersion] = useState<DeviceConfigurationVersion>();
  const [loadingEvidence, setLoadingEvidence] = useState(false);
  const [loadingConfiguration, setLoadingConfiguration] = useState(false);
  const [configurationModalOpen, setConfigurationModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reevaluating, setReevaluating] = useState(false);

  const canConfigure = mode === 'organization'
    && Boolean(asset && organizationCode);

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

  const loadConfiguration = async () => {
    if (!asset || !organizationCode || !canConfigure) return;
    setLoadingConfiguration(true);
    try {
      const page = await listDeviceConfigurationVersions(
        organizationCode,
        asset.deviceCode,
        { limit: 20 },
      );
      setVersions(page.items);
      if (page.items.length) {
        setLatestVersion(await getDeviceConfigurationVersion(
          organizationCode,
          asset.deviceCode,
          page.items[0].versionNo,
        ));
      } else {
        setLatestVersion(undefined);
      }
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setLoadingConfiguration(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    setEvidence([]);
    setVersions([]);
    setLatestVersion(undefined);
    void loadEvidence();
    void loadConfiguration();
    // The stable identities below intentionally define a new drawer target.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, mode, asset?.hardwareSn, asset?.deviceCode, organizationCode]);

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
                await loadEvidence();
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
      device: {
        displayName: latestVersion.device.displayName,
        address: latestVersion.device.address ?? undefined,
      },
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
                <Descriptions.Item label="投口数量">
                  {asset.expectedPortCount}
                </Descriptions.Item>
                <Descriptions.Item label="二维码">
                  {asset.miniappQrStatus}
                </Descriptions.Item>
                <Descriptions.Item label="OneNet 设备名" span={2}>
                  {asset.oneNetMapping.deviceName}
                </Descriptions.Item>
              </Descriptions>
            </Card>

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

            {canConfigure && (
              <section>
                <Space
                  align="center"
                  style={{ width: '100%', justifyContent: 'space-between' }}
                >
                  <Space>
                    <CloudSyncOutlined />
                    <Typography.Title level={5} style={{ margin: 0 }}>
                      日常价格与设备配置
                    </Typography.Title>
                  </Space>
                  <Button
                    icon={<EditOutlined />}
                    disabled={!latestVersion}
                    onClick={openConfigurationEditor}
                  >
                    基于最新版发布
                  </Button>
                </Space>
                <Alert
                  style={{ margin: '12px 0' }}
                  type="info"
                  showIcon
                  message="安装、通电和联网后无需机构确认"
                  description="系统会自动下发配置并测量厂家初始袋皮重；这里仅用于日常改价或调整投口配置。"
                />
                <Spin spinning={loadingConfiguration}>
                  {!versions.length ? (
                    <Empty description="系统正在创建并下发初始配置" />
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
                              </Space>
                            }
                            description={
                              `${version.deviceDisplayName} · ${version.publishedBy}`
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
        title="发布日常价格与设备配置"
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
          description="当前进行中的投递或清运继续使用启动时冻结的旧配置；新业务只在新版本精确应用后使用它。"
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
          <Space size={16} style={{ width: '100%' }} align="start">
            <Form.Item
              name={['device', 'displayName']}
              label="设备显示名"
              rules={[{ required: true }]}
              style={{ flex: 1 }}
            >
              <Input maxLength={100} />
            </Form.Item>
            <Form.Item
              name={['device', 'address']}
              label="安装地址"
              style={{ flex: 2 }}
            >
              <Input maxLength={500} />
            </Form.Item>
          </Space>
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
    </>
  );
}
