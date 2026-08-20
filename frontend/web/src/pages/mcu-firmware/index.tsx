import { useState, useRef } from 'react';
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
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  advanceMcuFirmwareWave,
  createMcuFirmwareRollout,
  getMcuFirmwareRollout,
  listMcuFirmwareReleases,
  listMcuFirmwareRollouts,
  promoteMcuFirmwareRollout,
  registerMcuFirmwareRelease,
  startMcuFirmwareValidation,
  stopMcuFirmwareRollout,
  type CreateMcuFirmwareRolloutRequest,
  type McuFirmwareDeployment,
  type McuFirmwareRelease,
  type McuFirmwareRollout,
  type RegisterMcuFirmwareReleaseRequest,
} from '@/api/mcuFirmware';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';

interface ReleaseFormValues extends RegisterMcuFirmwareReleaseRequest {}

interface RolloutFormValues {
  releaseUid: string;
  validationHardwareSn: string;
  targetHardwareSns: string;
  batchSize: number;
  reason: string;
}

interface ActionState {
  kind: 'validation' | 'promotion' | 'advance' | 'stop';
  rollout: McuFirmwareRollout;
}

const rolloutLabels: Record<string, string> = {
  DRAFT: '待启动验证',
  VALIDATING: '验证中',
  VALIDATION_FAILED: '验证失败',
  AWAITING_PROMOTION: '待人工推广',
  ACTIVE: '灰度进行中',
  COMPLETED: '已完成',
  STOPPED: '已停止',
};

const rolloutColors: Record<string, string> = {
  DRAFT: 'default',
  VALIDATING: 'processing',
  VALIDATION_FAILED: 'error',
  AWAITING_PROMOTION: 'warning',
  ACTIVE: 'processing',
  COMPLETED: 'success',
  STOPPED: 'default',
};

const deploymentLabels: Record<string, string> = {
  PENDING: '等待人工下发',
  QUEUED: '已进入可靠队列',
  PREFLIGHT: '升级前检查',
  PREPARED: '设备已进入安全态',
  FLASHING_TARGET: '正在烧录目标固件',
  VERIFYING_TARGET: '正在验证目标固件',
  ROLLING_BACK: '正在自动回滚',
  VERIFYING_ROLLBACK: '正在验证回滚固件',
  SUCCEEDED: '升级成功',
  ROLLED_BACK: '已回滚',
  FAILED_LOCKED: '失败并锁定',
  REJECTED: '设备拒绝',
};

function failureMessage(error: unknown) {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : 'MCU 固件操作失败';
}

function newUuid() {
  return crypto.randomUUID();
}

function splitHardwareSns(value: string) {
  return Array.from(new Set(
    value
      .split(/[\s,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean),
  ));
}

export default function McuFirmwarePage() {
  const executeCommand = useCommandExecutor();
  const releaseActionRef = useRef<ActionType>(null);
  const rolloutActionRef = useRef<ActionType>(null);
  const [releaseForm] = Form.useForm<ReleaseFormValues>();
  const [rolloutForm] = Form.useForm<RolloutFormValues>();
  const [actionForm] = Form.useForm<{ reason: string }>();
  const [releaseModalOpen, setReleaseModalOpen] = useState(false);
  const [rolloutModalOpen, setRolloutModalOpen] = useState(false);
  const [releaseOptions, setReleaseOptions] = useState<McuFirmwareRelease[]>([]);
  const [selected, setSelected] = useState<McuFirmwareRollout>();
  const [action, setAction] = useState<ActionState>();
  const [submitting, setSubmitting] = useState(false);

  const refreshRollout = async (rolloutUid: string) => {
    const current = await getMcuFirmwareRollout(rolloutUid);
    setSelected(current);
    await rolloutActionRef.current?.reload();
  };

  const openReleaseModal = () => {
    releaseForm.resetFields();
    releaseForm.setFieldsValue({
      releaseUid: newUuid(),
      hardwareCompatibility: 'STM32F103C8T6',
      fixedFrameRevision: 2,
    });
    setReleaseModalOpen(true);
  };

  const submitRelease = async () => {
    const values = await releaseForm.validateFields();
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey('mcu-firmware-release', values.releaseUid, values),
        (intent) => registerMcuFirmwareRelease(values, intent),
      );
      message.success('固件发布已登记；后端不会修改或重新签名该包');
      setReleaseModalOpen(false);
      await releaseActionRef.current?.reload();
    } catch (error) {
      message.error(failureMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const openRolloutModal = async () => {
    try {
      const page = await listMcuFirmwareReleases({ page: 1, pageSize: 100 });
      setReleaseOptions(page.items.filter((item) => item.status !== 'ARCHIVED'));
      rolloutForm.resetFields();
      rolloutForm.setFieldsValue({ batchSize: 10 });
      setRolloutModalOpen(true);
    } catch (error) {
      message.error(failureMessage(error));
    }
  };

  const submitRollout = async () => {
    const values = await rolloutForm.validateFields();
    const payload: CreateMcuFirmwareRolloutRequest = {
      releaseUid: values.releaseUid,
      validationHardwareSn: values.validationHardwareSn.trim(),
      targetHardwareSns: splitHardwareSns(values.targetHardwareSns),
      batchSize: values.batchSize,
      reason: values.reason.trim(),
    };
    setSubmitting(true);
    try {
      const created = await executeCommand(
        commandKey('mcu-firmware-rollout', payload.releaseUid, payload),
        (intent) => createMcuFirmwareRollout(payload, intent),
      );
      message.success('灰度计划已创建，尚未向任何设备下发');
      setRolloutModalOpen(false);
      setSelected(created);
      await rolloutActionRef.current?.reload();
    } catch (error) {
      message.error(failureMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const submitAction = async () => {
    if (!action) return;
    const { reason } = await actionForm.validateFields();
    const normalizedReason = reason.trim();
    setSubmitting(true);
    try {
      const operation = {
        validation: startMcuFirmwareValidation,
        promotion: promoteMcuFirmwareRollout,
        advance: advanceMcuFirmwareWave,
        stop: stopMcuFirmwareRollout,
      }[action.kind];
      const result = await executeCommand(
        commandKey(
          `mcu-firmware-${action.kind}`,
          action.rollout.rolloutUid,
          { reason: normalizedReason },
        ),
        (intent) => operation(
          action.rollout.rolloutUid,
          normalizedReason,
          intent,
        ),
      );
      setSelected(result);
      setAction(undefined);
      actionForm.resetFields();
      message.success({
        validation: '单设备验证命令已进入可靠队列',
        promotion: '已人工确认推广；仍需手动下发第一批',
        advance: result.status === 'COMPLETED'
          ? '所有批次已确认完成'
          : `第 ${result.currentWaveNo} 批已进入可靠队列`,
        stop: '灰度计划已停止，未下发设备不会升级',
      }[action.kind]);
      await rolloutActionRef.current?.reload();
    } catch (error) {
      message.error(failureMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const releaseColumns: ProColumns<McuFirmwareRelease>[] = [
    {
      title: '固件版本',
      dataIndex: 'firmwareVersion',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{row.firmwareVersion}</Typography.Text>
          <Typography.Text type="secondary">
            版本码 {row.firmwareVersionCode}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '固件身份',
      dataIndex: 'firmwareIdentityHex',
      search: false,
      render: (_, row) => (
        <Typography.Text code copyable>{row.firmwareIdentityHex}</Typography.Text>
      ),
    },
    {
      title: '适配边界',
      dataIndex: 'hardwareCompatibility',
      search: false,
      render: (_, row) => `${row.hardwareCompatibility} · 固定帧 v${row.fixedFrameRevision}`,
    },
    {
      title: '状态',
      dataIndex: 'status',
      search: false,
      render: (_, row) => (
        <Tag color={row.status === 'PROMOTED' ? 'success' : 'default'}>
          {row.status === 'PROMOTED' ? '已通过验证推广' : '待验证'}
        </Tag>
      ),
    },
    {
      title: '包',
      dataIndex: 'packageSize',
      search: false,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{row.packageSize} 字节</Typography.Text>
          <Typography.Text type="secondary" copyable>
            {row.packageSha256.slice(0, 16)}…
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '登记时间',
      dataIndex: 'createdAt',
      search: false,
      render: (_, row) => formatShanghaiTime(row.createdAt),
    },
  ];

  const rolloutColumns: ProColumns<McuFirmwareRollout>[] = [
    {
      title: '目标版本',
      dataIndex: ['release', 'firmwareVersion'],
      render: (_, row) => (
        <Typography.Link onClick={() => refreshRollout(row.rolloutUid)}>
          {row.release.firmwareVersion}
        </Typography.Link>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      search: false,
      render: (_, row) => (
        <Tag color={rolloutColors[row.status]}>
          {rolloutLabels[row.status] ?? row.status}
        </Tag>
      ),
    },
    {
      title: '验证设备',
      dataIndex: 'validationHardwareSn',
      search: false,
    },
    {
      title: '批次',
      dataIndex: 'currentWaveNo',
      search: false,
      render: (_, row) => `${Math.max(0, row.currentWaveNo)} / ${row.maximumWaveNo}`,
    },
    {
      title: '结果',
      dataIndex: 'succeededCount',
      search: false,
      render: (_, row) => (
        <Space size={12}>
          <Typography.Text type="success">成功 {row.succeededCount}</Typography.Text>
          <Typography.Text type={row.rolledBackCount ? 'warning' : 'secondary'}>
            回滚 {row.rolledBackCount}
          </Typography.Text>
          <Typography.Text type={row.failedCount ? 'danger' : 'secondary'}>
            失败 {row.failedCount}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      search: false,
      render: (_, row) => formatShanghaiTime(row.createdAt),
    },
  ];

  const deploymentColumns = [
    {
      title: '批次',
      dataIndex: 'waveNo',
      width: 90,
      render: (waveNo: number, row: McuFirmwareDeployment) =>
        row.kind === 'VALIDATION' ? <Tag color="blue">验证机</Tag> : `第 ${waveNo} 批`,
    },
    { title: '设备 SN', dataIndex: 'hardwareSn', width: 190 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 180,
      render: (status: string) => (
        <Tag color={
          status === 'SUCCEEDED'
            ? 'success'
            : ['ROLLED_BACK', 'FAILED_LOCKED', 'REJECTED'].includes(status)
              ? 'error'
              : status === 'PENDING' ? 'default' : 'processing'
        }>
          {deploymentLabels[status] ?? status}
        </Tag>
      ),
    },
    {
      title: '尝试',
      key: 'attempts',
      width: 110,
      render: (_: unknown, row: McuFirmwareDeployment) =>
        `目标 ${row.targetAttemptCount} / 回滚 ${row.rollbackAttemptCount}`,
    },
    {
      title: '已安装固件',
      key: 'installed',
      render: (_: unknown, row: McuFirmwareDeployment) =>
        row.installedFirmwareVersion
          ? `${row.installedFirmwareVersion} · ${row.installedFirmwareIdentityHex}`
          : '尚无终态证明',
    },
    { title: '错误码', dataIndex: 'errorCode', render: (value?: string) => value ?? '—' },
  ];

  const actionCopy = action && {
    validation: {
      title: '启动单设备验证',
      warning: '只会向验证设备下发。验证成功后仍不会自动推广。',
    },
    promotion: {
      title: '确认推广该固件',
      warning: '确认后固件进入可灰度状态，但第一批仍要另行手动下发。',
    },
    advance: {
      title: action.rollout.currentWaveNo >= action.rollout.maximumWaveNo
        ? '确认灰度全部完成'
        : '下发下一批设备',
      warning: '系统会先确认上一批全部成功；任一回滚或失败都会阻止本次操作。',
    },
    stop: {
      title: '停止灰度计划',
      warning: '已完成的设备不会回退；尚未下发的设备保持原固件。',
    },
  }[action.kind];

  return (
    <PageContainer
      {...pageHeader(
        'MCU 固件灰度',
        '管理离线签名固件包，先做单设备验证，再由平台管理员逐批推进。系统不会自动推广或跳过失败批次。',
      )}
    >
      <Alert
        showIcon
        type="warning"
        style={{ marginBottom: 16 }}
        message="固件包必须先离线签名并上传到私有 COS"
        description="后端只登记版本、固件身份、对象路径和 SHA-256，不持有签名私钥。设备获得的临时凭证只能读取当前发布目录。"
      />
      <Tabs
        items={[
          {
            key: 'rollouts',
            label: '灰度计划',
            children: (
              <ProTable<McuFirmwareRollout>
                {...proTableConfig}
                actionRef={rolloutActionRef}
                rowKey="rolloutUid"
                columns={rolloutColumns}
                search={false}
                request={async (params) => {
                  const result = await listMcuFirmwareRollouts({
                    page: params.current,
                    pageSize: params.pageSize,
                  });
                  return { data: result.items, total: result.total, success: true };
                }}
                toolBarRender={() => [
                  <Button key="create" type="primary" onClick={openRolloutModal}>
                    创建灰度计划
                  </Button>,
                ]}
              />
            ),
          },
          {
            key: 'releases',
            label: '固件发布',
            children: (
              <ProTable<McuFirmwareRelease>
                {...proTableConfig}
                actionRef={releaseActionRef}
                rowKey="releaseUid"
                columns={releaseColumns}
                search={false}
                request={async (params) => {
                  const result = await listMcuFirmwareReleases({
                    page: params.current,
                    pageSize: params.pageSize,
                  });
                  return { data: result.items, total: result.total, success: true };
                }}
                toolBarRender={() => [
                  <Button key="register" type="primary" onClick={openReleaseModal}>
                    登记签名固件包
                  </Button>,
                ]}
              />
            ),
          },
        ]}
      />

      <Modal
        title="登记离线签名固件包"
        open={releaseModalOpen}
        width={720}
        confirmLoading={submitting}
        onOk={submitRelease}
        onCancel={() => setReleaseModalOpen(false)}
      >
        <Alert
          showIcon
          type="info"
          message="对象路径必须为 ecobin/mcu-firmware/{发布编号}/{包 SHA-256}.efw"
          style={{ marginBottom: 16 }}
        />
        <Form form={releaseForm} layout="vertical">
          <Form.Item name="releaseUid" label="发布编号" rules={[{ required: true }]}>
            <Input addonAfter={<Button type="link" onClick={() => releaseForm.setFieldValue('releaseUid', newUuid())}>重新生成</Button>} />
          </Form.Item>
          <Space align="start" size={16} style={{ width: '100%' }}>
            <Form.Item name="firmwareVersion" label="语义版本" rules={[{ required: true }]}>
              <Input placeholder="2.1.0" />
            </Form.Item>
            <Form.Item name="firmwareVersionCode" label="单调版本码" rules={[{ required: true }]}>
              <InputNumber min={1} max={4294967295} style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="firmwareIdentityHex" label="固件身份（8 字节十六进制）" rules={[{ required: true, pattern: /^[0-9a-f]{16}$/ }]}>
              <Input placeholder="0123456789abcdef" />
            </Form.Item>
          </Space>
          <Space align="start" size={16}>
            <Form.Item name="hardwareCompatibility" label="兼容芯片" rules={[{ required: true }]}>
              <Input disabled />
            </Form.Item>
            <Form.Item name="fixedFrameRevision" label="UART 固定帧修订号" rules={[{ required: true }]}>
              <InputNumber disabled />
            </Form.Item>
            <Form.Item name="packageSize" label="包大小（字节）" rules={[{ required: true }]}>
              <InputNumber min={1} max={131072} />
            </Form.Item>
          </Space>
          <Form.Item name="packageSha256" label="包 SHA-256" rules={[{ required: true, pattern: /^[0-9a-f]{64}$/ }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="objectKey"
            label="私有 COS 对象路径"
            rules={[{ required: true }]}
            extra={(
              <Button
                type="link"
                style={{ paddingInline: 0 }}
                onClick={() => {
                  const releaseUid = releaseForm.getFieldValue('releaseUid');
                  const digest = releaseForm.getFieldValue('packageSha256');
                  if (releaseUid && digest) {
                    releaseForm.setFieldValue(
                      'objectKey',
                      `ecobin/mcu-firmware/${releaseUid}/${digest}.efw`,
                    );
                  } else {
                    message.warning('请先填写发布编号和包 SHA-256');
                  }
                }}
              >
                按发布编号和摘要生成路径
              </Button>
            )}
          >
            <Input />
          </Form.Item>
          <Form.Item name="releaseNotes" label="发布说明">
            <Input.TextArea rows={3} maxLength={1000} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="创建 MCU 固件灰度计划"
        open={rolloutModalOpen}
        width={680}
        confirmLoading={submitting}
        onOk={submitRollout}
        onCancel={() => setRolloutModalOpen(false)}
      >
        <Form form={rolloutForm} layout="vertical">
          <Form.Item name="releaseUid" label="目标固件" rules={[{ required: true }]}>
            <Select
              options={releaseOptions.map((item) => ({
                value: item.releaseUid,
                label: `${item.firmwareVersion} · ${item.firmwareIdentityHex}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="validationHardwareSn" label="单设备验证机 SN" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="targetHardwareSns"
            label="后续灰度目标 SN"
            rules={[{ required: true }]}
            extra="每行一个，也可用逗号分隔。若重复写入验证机，系统会自动去除。"
          >
            <Input.TextArea rows={6} />
          </Form.Item>
          <Form.Item name="batchSize" label="每批设备数" rules={[{ required: true }]}>
            <InputNumber min={1} max={100} />
          </Form.Item>
          <Form.Item name="reason" label="创建原因" rules={[{ required: true }]}>
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={selected ? `固件灰度 ${selected.release.firmwareVersion}` : '固件灰度详情'}
        open={Boolean(selected)}
        width={1040}
        onClose={() => setSelected(undefined)}
        extra={selected && (
          <Space>
            {selected.status === 'DRAFT' && (
              <Button type="primary" onClick={() => setAction({ kind: 'validation', rollout: selected })}>
                启动单设备验证
              </Button>
            )}
            {selected.status === 'AWAITING_PROMOTION' && (
              <Button type="primary" danger onClick={() => setAction({ kind: 'promotion', rollout: selected })}>
                人工确认推广
              </Button>
            )}
            {selected.status === 'ACTIVE' && (
              <>
                <Button type="primary" onClick={() => setAction({ kind: 'advance', rollout: selected })}>
                  {selected.currentWaveNo >= selected.maximumWaveNo ? '确认全部完成' : '下发下一批'}
                </Button>
                <Button danger onClick={() => setAction({ kind: 'stop', rollout: selected })}>
                  停止计划
                </Button>
              </>
            )}
            <Button onClick={() => refreshRollout(selected.rolloutUid)}>刷新</Button>
          </Space>
        )}
      >
        {selected && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            {(selected.failedCount > 0 || selected.rolledBackCount > 0) && (
              <Alert
                showIcon
                type="error"
                message="当前计划存在回滚或失败设备"
                description="系统不会自动下发下一批。失败锁定设备会停止业务，需通过现场或受控 SSH 工具处理。"
              />
            )}
            <Descriptions bordered size="small" column={3}>
              <Descriptions.Item label="状态">
                <Tag color={rolloutColors[selected.status]}>{rolloutLabels[selected.status]}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="目标版本">{selected.release.firmwareVersion}</Descriptions.Item>
              <Descriptions.Item label="固件身份"><Typography.Text code>{selected.release.firmwareIdentityHex}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="验证设备">{selected.validationHardwareSn}</Descriptions.Item>
              <Descriptions.Item label="当前批次">{Math.max(0, selected.currentWaveNo)} / {selected.maximumWaveNo}</Descriptions.Item>
              <Descriptions.Item label="每批设备数">{selected.batchSize}</Descriptions.Item>
              <Descriptions.Item label="创建原因" span={3}>{selected.reason}</Descriptions.Item>
            </Descriptions>
            <Table<McuFirmwareDeployment>
              rowKey="deploymentUid"
              size="small"
              pagination={false}
              columns={deploymentColumns}
              dataSource={selected.deployments}
              scroll={{ x: 980 }}
            />
          </Space>
        )}
      </Drawer>

      <Modal
        title={actionCopy?.title}
        open={Boolean(action)}
        confirmLoading={submitting}
        okButtonProps={{ danger: action?.kind === 'promotion' || action?.kind === 'stop' }}
        onOk={submitAction}
        onCancel={() => setAction(undefined)}
      >
        <Alert showIcon type="warning" message={actionCopy?.warning} style={{ marginBottom: 16 }} />
        <Form form={actionForm} layout="vertical">
          <Form.Item name="reason" label="本次人工操作原因" rules={[{ required: true }]}>
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
