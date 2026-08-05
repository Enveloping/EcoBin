import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  KeyOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import {
  allocatePlatformDeviceAssetToTenant,
  clearPlatformDeviceMaintenanceIsolation,
  confirmPlatformOneNetCredentialRotation,
  getPlatformDeviceAsset,
  listPlatformDeviceAssetAllocations,
  reclaimPlatformDeviceAssetAllocation,
  type DeviceAsset,
  type DeviceTenantAllocation,
} from '@/api/deviceDirectory';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';
import {
  allocationColors,
  allocationLabels,
  assetColors,
  assetLabels,
  blockerLabel,
} from './devicePresentation';

interface DeviceAssetDrawerProps {
  open: boolean;
  hardwareSn?: string;
  tenantOptions: Array<{ label: string; value: string }>;
  onClose: () => void;
  onUpdated: () => void;
}

interface AllocateFormValues {
  tenantCode: string;
  reason?: string;
}

interface ReclaimFormValues {
  mode: 'NORMAL' | 'EXCEPTIONAL';
  physicalPossessionConfirmed?: boolean;
  reason: string;
}

interface ClearanceFormValues {
  physicalPossessionConfirmed?: boolean;
  inspectionConfirmed?: boolean;
  reason: string;
}

function problemMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '设备资产详情加载失败';
}

function blockersFrom(error: unknown): string[] {
  if (!(error instanceof ApiProblem)) return [];
  const blockers = error.details.blockers;
  return Array.isArray(blockers)
    ? blockers.filter((value): value is string => typeof value === 'string')
    : [];
}

export default function DeviceAssetDrawer({
  open,
  hardwareSn,
  tenantOptions,
  onClose,
  onUpdated,
}: DeviceAssetDrawerProps) {
  const executeCommand = useCommandExecutor();
  const [allocateForm] = Form.useForm<AllocateFormValues>();
  const [reclaimForm] = Form.useForm<ReclaimFormValues>();
  const [clearanceForm] = Form.useForm<ClearanceFormValues>();
  const requestSequence = useRef(0);
  const selectedTarget = useRef<string>();
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string>();
  const [asset, setAsset] = useState<DeviceAsset>();
  const [allocations, setAllocations] = useState<DeviceTenantAllocation[]>([]);
  const [allocateOpen, setAllocateOpen] = useState(false);
  const [reclaiming, setReclaiming] = useState<DeviceTenantAllocation>();
  const [clearanceOpen, setClearanceOpen] = useState(false);
  const [rotationRequired, setRotationRequired] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [commandError, setCommandError] = useState<string>();
  const [commandBlockers, setCommandBlockers] = useState<string[]>([]);

  selectedTarget.current = open ? hardwareSn : undefined;
  const currentAllocation = allocations.find(
    (allocation) => allocation.allocationStatus === 'ACTIVE',
  );

  const load = useCallback(async () => {
    if (!open || !hardwareSn) return;
    const sequence = ++requestSequence.current;
    setLoading(true);
    setLoadError(undefined);
    setAsset(undefined);
    setAllocations([]);
    try {
      const [loadedAsset, allocationPage] = await Promise.all([
        getPlatformDeviceAsset(hardwareSn),
        listPlatformDeviceAssetAllocations({
          hardwareSn,
          page: 1,
          pageSize: 200,
        }),
      ]);
      if (
        sequence !== requestSequence.current
        || selectedTarget.current !== hardwareSn
      ) return;
      setAsset(loadedAsset);
      setAllocations(allocationPage.items);
    } catch (error) {
      if (
        sequence === requestSequence.current
        && selectedTarget.current === hardwareSn
      ) setLoadError(problemMessage(error));
    } finally {
      if (
        sequence === requestSequence.current
        && selectedTarget.current === hardwareSn
      ) setLoading(false);
    }
  }, [hardwareSn, open]);

  useEffect(() => {
    if (open) void load();
    return () => {
      requestSequence.current += 1;
    };
  }, [load, open]);

  const resetCommandFeedback = () => {
    setCommandError(undefined);
    setCommandBlockers([]);
  };

  const reloadAfterConflict = async (error: unknown) => {
    if (error instanceof ApiProblem && error.isVersionConflict) {
      setAllocateOpen(false);
      setReclaiming(undefined);
      setClearanceOpen(false);
      setRotationRequired(false);
      allocateForm.resetFields();
      reclaimForm.resetFields();
      clearanceForm.resetFields();
      resetCommandFeedback();
      message.warning('资产或分配状态已更新，已刷新详情，请重新确认');
      await load();
    }
  };

  const submitAllocation = async () => {
    if (!asset) return;
    const values = await allocateForm.validateFields();
    const payload = {
      hardwareSn: asset.hardwareSn,
      expectedAssetVersion: asset.version,
      reason: values.reason?.trim() || null,
    };
    resetCommandFeedback();
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'device.allocation.create',
          `${values.tenantCode}:${asset.hardwareSn}`,
          payload,
        ),
        (intent) => allocatePlatformDeviceAssetToTenant(
          values.tenantCode,
          payload,
          intent,
        ),
      );
      message.success('设备已分配到租户设备池，尚未选择机构');
      setAllocateOpen(false);
      setRotationRequired(false);
      await load();
      onUpdated();
    } catch (error) {
      if (
        error instanceof ApiProblem
        && error.code === 'DEVICE.CREDENTIAL_ROTATION_REQUIRED'
      ) {
        setRotationRequired(true);
        setCommandError(
          '该设备将跨租户重新分配。请先在 OneNet 线下更换 Device Key，再确认已轮换。EcoBin 不接收或保存密钥值。',
        );
      } else {
        setCommandError(problemMessage(error));
        setCommandBlockers(blockersFrom(error));
        await reloadAfterConflict(error);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const confirmRotation = async () => {
    if (!asset) return;
    const values = await allocateForm.validateFields();
    const reason = values.reason?.trim()
      || `为分配到租户 ${values.tenantCode} 完成线下 Device Key 轮换`;
    const payload = { expectedAssetVersion: asset.version, reason };
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey('device.credential.rotate.confirm', asset.hardwareSn, payload),
        (intent) => confirmPlatformOneNetCredentialRotation(
          asset.hardwareSn,
          payload,
          intent,
        ),
      );
      message.success('已记录密钥轮换确认，请重新提交租户分配');
      setRotationRequired(false);
      resetCommandFeedback();
      await load();
      onUpdated();
    } catch (error) {
      setCommandError(problemMessage(error));
      await reloadAfterConflict(error);
    } finally {
      setSubmitting(false);
    }
  };

  const submitReclaim = async () => {
    if (!reclaiming) return;
    const values = await reclaimForm.validateFields();
    const payload = {
      expectedAllocationVersion: reclaiming.allocationVersion,
      expectedAssetVersion: reclaiming.assetVersion,
      mode: values.mode,
      physicalPossessionConfirmed:
        values.physicalPossessionConfirmed === true,
      reason: values.reason.trim(),
    };
    resetCommandFeedback();
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey('device.allocation.reclaim', reclaiming.allocationUid, payload),
        (intent) => reclaimPlatformDeviceAssetAllocation(
          reclaiming.allocationUid,
          payload,
          intent,
        ),
      );
      message.success(
        values.mode === 'NORMAL'
          ? '设备已正常收回平台库存'
          : '设备已异常收回并进入维修隔离',
      );
      setReclaiming(undefined);
      await load();
      onUpdated();
    } catch (error) {
      setCommandError(problemMessage(error));
      setCommandBlockers(blockersFrom(error));
      await reloadAfterConflict(error);
    } finally {
      setSubmitting(false);
    }
  };

  const submitClearance = async () => {
    if (!asset) return;
    const values = await clearanceForm.validateFields();
    const payload = {
      expectedAssetVersion: asset.version,
      physicalPossessionConfirmed:
        values.physicalPossessionConfirmed === true,
      inspectionConfirmed: values.inspectionConfirmed === true,
      reason: values.reason.trim(),
    };
    resetCommandFeedback();
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey('device.maintenance.clear', asset.hardwareSn, payload),
        (intent) => clearPlatformDeviceMaintenanceIsolation(
          asset.hardwareSn,
          payload,
          intent,
        ),
      );
      message.success('维修隔离已解除，设备已回到平台库存');
      setClearanceOpen(false);
      await load();
      onUpdated();
    } catch (error) {
      setCommandError(problemMessage(error));
      setCommandBlockers(blockersFrom(error));
      await reloadAfterConflict(error);
    } finally {
      setSubmitting(false);
    }
  };

  const commandAlert = commandError ? (
    <Alert
      showIcon
      type="error"
      message={commandError}
      description={commandBlockers.length ? (
        <ul style={{ margin: 0, paddingInlineStart: 20 }}>
          {commandBlockers.map((blocker) => (
            <li key={blocker}>{blockerLabel(blocker)}</li>
          ))}
        </ul>
      ) : undefined}
      style={{ marginBottom: 16 }}
    />
  ) : null;

  return (
    <>
      <Drawer
        title={(
          <Space>
            <span>平台资产详情</span>
            {hardwareSn && (
              <Typography.Text type="secondary" copyable>
                {hardwareSn}
              </Typography.Text>
            )}
          </Space>
        )}
        open={open}
        width={920}
        onClose={onClose}
        destroyOnClose
        extra={(
          <Space>
            <Button
              icon={<ReloadOutlined spin={loading} />}
              onClick={() => void load()}
              disabled={loading}
            >
              刷新
            </Button>
            {asset?.lifecycleStatus === 'IN_STOCK' && (
              <Button
                type="primary"
                onClick={() => {
                  resetCommandFeedback();
                  setRotationRequired(false);
                  allocateForm.resetFields();
                  setAllocateOpen(true);
                }}
              >
                分配给租户
              </Button>
            )}
            {asset?.lifecycleStatus === 'MAINTENANCE' && (
              <Button
                type="primary"
                icon={<SafetyCertificateOutlined />}
                onClick={() => {
                  resetCommandFeedback();
                  clearanceForm.resetFields();
                  setClearanceOpen(true);
                }}
              >
                解除维修隔离
              </Button>
            )}
          </Space>
        )}
      >
        {loading && !asset ? (
          <div style={{ padding: '72px 0', textAlign: 'center' }}>
            <Spin tip="正在读取资产与分配事实" />
          </div>
        ) : loadError && !asset ? (
          <Alert
            showIcon
            type="error"
            message="资产详情加载失败"
            description={loadError}
            action={<Button onClick={() => void load()}>重新加载</Button>}
          />
        ) : asset ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            {loadError && (
              <Alert showIcon type="error" message={loadError} />
            )}
            <Descriptions title="资产资料" bordered size="small" column={2}>
              <Descriptions.Item label="硬件序列号">
                <Typography.Text copyable>{asset.hardwareSn}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="资产状态">
                <Tag color={assetColors[asset.lifecycleStatus]}>
                  {assetLabels[asset.lifecycleStatus]}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="设备型号">
                {asset.modelCode}
              </Descriptions.Item>
              <Descriptions.Item label="生产批次">
                {asset.productionBatch || '—'}
              </Descriptions.Item>
              <Descriptions.Item label="预期投口数">
                {asset.expectedPortCount}
              </Descriptions.Item>
              <Descriptions.Item label="资产版本">
                v{asset.version}
              </Descriptions.Item>
              <Descriptions.Item label="登记时间">
                {formatShanghaiTime(asset.createdAt)}
              </Descriptions.Item>
              <Descriptions.Item label="最后更新">
                {formatShanghaiTime(asset.updatedAt)}
              </Descriptions.Item>
            </Descriptions>

            <Descriptions title="OneNet 计算映射" bordered size="small" column={2}>
              <Descriptions.Item label="产品 ID">
                {asset.oneNetMapping?.productId || '—'}
              </Descriptions.Item>
              <Descriptions.Item label="设备名">
                {asset.oneNetMapping?.deviceName ? (
                  <Typography.Text copyable>
                    {asset.oneNetMapping.deviceName}
                  </Typography.Text>
                ) : '—'}
              </Descriptions.Item>
            </Descriptions>

            <Descriptions title="当前归属与部署" bordered size="small" column={2}>
              <Descriptions.Item label="当前租户">
                {currentAllocation?.tenantCode || '尚未分配'}
              </Descriptions.Item>
              <Descriptions.Item label="分配状态">
                {currentAllocation ? (
                  <Tag color={allocationColors[currentAllocation.allocationStatus]}>
                    {allocationLabels[currentAllocation.allocationStatus]}
                  </Tag>
                ) : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="当前部署">
                {asset.currentDeployment ? (
                  <Space direction="vertical" size={0}>
                    <Typography.Text copyable>
                      {asset.currentDeployment.deploymentCode}
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      {asset.currentDeployment.tenantCode}
                      {' / '}
                      {asset.currentDeployment.organizationCode}
                    </Typography.Text>
                  </Space>
                ) : '尚未部署'}
              </Descriptions.Item>
              <Descriptions.Item label="平台收回">
                {currentAllocation ? (
                  <Space>
                    <Button
                      size="small"
                      onClick={() => {
                        resetCommandFeedback();
                        reclaimForm.setFieldsValue({
                          mode: 'NORMAL',
                          physicalPossessionConfirmed: false,
                          reason: '',
                        });
                        setReclaiming(currentAllocation);
                      }}
                    >
                      正常 / 异常收回
                    </Button>
                  </Space>
                ) : '—'}
              </Descriptions.Item>
            </Descriptions>

            <div>
              <Typography.Title level={5}>租户分配历史</Typography.Title>
              {allocations.length ? (
                <Table<DeviceTenantAllocation>
                  size="small"
                  rowKey="allocationUid"
                  pagination={false}
                  dataSource={allocations}
                  scroll={{ x: 920 }}
                  columns={[
                    { title: '租户', dataIndex: 'tenantCode', width: 160 },
                    {
                      title: '状态',
                      dataIndex: 'allocationStatus',
                      width: 100,
                      render: (value: DeviceTenantAllocation['allocationStatus']) => (
                        <Tag color={allocationColors[value]}>
                          {allocationLabels[value]}
                        </Tag>
                      ),
                    },
                    {
                      title: '分配时间',
                      dataIndex: 'allocatedAt',
                      width: 180,
                      render: (value) => formatShanghaiTime(value),
                    },
                    {
                      title: '结束时间',
                      dataIndex: 'endedAt',
                      width: 180,
                      render: (value) => value ? formatShanghaiTime(value) : '—',
                    },
                    { title: '结束方式', dataIndex: 'endMode', width: 110 },
                    { title: '原因', dataIndex: 'endReason' },
                  ]}
                />
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="这台资产还没有租户分配记录"
                />
              )}
            </div>
          </Space>
        ) : null}
      </Drawer>

      <Modal
        title="分配给租户"
        open={allocateOpen}
        okText="提交分配"
        confirmLoading={submitting}
        onOk={() => void submitAllocation()}
        onCancel={() => !submitting && setAllocateOpen(false)}
        destroyOnClose
        footer={rotationRequired ? (
          <Space>
            <Button onClick={() => setAllocateOpen(false)} disabled={submitting}>
              取消
            </Button>
            <Button
              type="primary"
              icon={<KeyOutlined />}
              loading={submitting}
              onClick={() => void confirmRotation()}
            >
              确认已轮换 Device Key
            </Button>
          </Space>
        ) : undefined}
      >
        <Alert
          showIcon
          type="info"
          message="分配只确定租户归属"
          description="此处不选择机构，也不开启经营。分配完成后，租户再从设备池发起机构部署。"
          style={{ marginBottom: 16 }}
        />
        {commandAlert}
        <Form<AllocateFormValues>
          form={allocateForm}
          layout="vertical"
          disabled={submitting}
        >
          <Form.Item
            name="tenantCode"
            label="目标租户"
            rules={[{ required: true, message: '请选择目标租户' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              options={tenantOptions}
              placeholder="选择租户"
            />
          </Form.Item>
          <Form.Item
            name="reason"
            label="分配原因"
            rules={[{ max: 500, message: '最多 500 个字符' }]}
          >
            <Input.TextArea rows={3} placeholder="可选" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="平台收回设备"
        open={!!reclaiming}
        okText="确认收回"
        confirmLoading={submitting}
        onOk={() => void submitReclaim()}
        onCancel={() => !submitting && setReclaiming(undefined)}
        destroyOnClose
      >
        <Alert
          showIcon
          type="warning"
          message="请以实物已收回为事实依据"
          description="正常收回会检查经营、作业、离线和待上传事实；异常收回会直接进入维修隔离，后续不能分配。"
          style={{ marginBottom: 16 }}
        />
        {commandAlert}
        <Form<ReclaimFormValues>
          form={reclaimForm}
          layout="vertical"
          disabled={submitting}
        >
          <Form.Item name="mode" label="收回方式" rules={[{ required: true }]}>
            <Select options={[
              { label: '正常收回（回平台库存）', value: 'NORMAL' },
              { label: '异常收回（进维修隔离）', value: 'EXCEPTIONAL' },
            ]} />
          </Form.Item>
          <Form.Item
            name="physicalPossessionConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('必须确认实物已收回')),
            }]}
          >
            <Checkbox>我确认设备实物已由平台收回</Checkbox>
          </Form.Item>
          <Form.Item
            name="reason"
            label="收回原因"
            rules={[
              { required: true, message: '请填写收回原因' },
              { max: 500, message: '最多 500 个字符' },
            ]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="解除维修隔离"
        open={clearanceOpen}
        okText="确认解除"
        confirmLoading={submitting}
        onOk={() => void submitClearance()}
        onCancel={() => !submitting && setClearanceOpen(false)}
        destroyOnClose
      >
        {commandAlert}
        <Form<ClearanceFormValues>
          form={clearanceForm}
          layout="vertical"
          disabled={submitting}
        >
          <Form.Item
            name="physicalPossessionConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请确认实物已收回')),
            }]}
          >
            <Checkbox>设备实物已在平台手中</Checkbox>
          </Form.Item>
          <Form.Item
            name="inspectionConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, value) => value
                ? Promise.resolve()
                : Promise.reject(new Error('请确认检查已通过')),
            }]}
          >
            <Checkbox>维修检查已通过，可重新入库</Checkbox>
          </Form.Item>
          <Form.Item
            name="reason"
            label="解除原因"
            rules={[
              { required: true, message: '请填写检查结论' },
              { max: 500, message: '最多 500 个字符' },
            ]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
