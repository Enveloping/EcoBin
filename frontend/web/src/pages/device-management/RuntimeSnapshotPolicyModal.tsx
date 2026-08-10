import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  getPlatformRuntimeSnapshotPolicy,
  releasePlatformRuntimeSnapshotPolicy,
  type RuntimeSnapshotPolicy,
} from '@/api/deviceDirectory';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';

interface Props {
  open: boolean;
  onClose: () => void;
}

interface FormValues {
  fallbackIntervalMinutes: number;
  reason: string;
}

function errorText(error: unknown) {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '运行快照策略操作失败';
}

export default function RuntimeSnapshotPolicyModal({ open, onClose }: Props) {
  const [form] = Form.useForm<FormValues>();
  const executeCommand = useCommandExecutor();
  const [policy, setPolicy] = useState<RuntimeSnapshotPolicy>();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const refreshSequence = useRef(0);
  const submittingRef = useRef(false);

  const refresh = async (quiet = false) => {
    if (submittingRef.current) return;
    const sequence = ++refreshSequence.current;
    if (!quiet) setLoading(true);
    try {
      const current = await getPlatformRuntimeSnapshotPolicy();
      if (sequence !== refreshSequence.current) return;
      setPolicy(current);
      if (!form.isFieldTouched('fallbackIntervalMinutes')) {
        form.setFieldValue(
          'fallbackIntervalMinutes',
          current.fallbackIntervalMinutes,
        );
      }
    } catch (error) {
      if (!quiet && sequence === refreshSequence.current) {
        message.error(errorText(error));
      }
    } finally {
      if (sequence === refreshSequence.current) setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return undefined;
    form.resetFields();
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 3000);
    return () => {
      window.clearInterval(timer);
      refreshSequence.current += 1;
    };
  }, [open]);

  const progress = useMemo(() => {
    if (!policy) return 0;
    if (policy.targetDeviceCount === 0) {
      return policy.rolloutStatus === 'DONE' ? 100 : 0;
    }
    return Math.min(
      100,
      Math.round(
        (policy.processedDeviceCount / policy.targetDeviceCount) * 100,
      ),
    );
  }, [policy]);

  const submit = async () => {
    if (!policy) return;
    const values = await form.validateFields();
    const payload = {
      expectedVersion: policy.version,
      fallbackIntervalMinutes: values.fallbackIntervalMinutes,
      reason: values.reason.trim(),
    };
    submittingRef.current = true;
    refreshSequence.current += 1;
    setLoading(false);
    setSubmitting(true);
    try {
      const released = await executeCommand(
        commandKey(
          'device.runtime-snapshot-policy.release',
          'GLOBAL',
          payload,
        ),
        (intent) => releasePlatformRuntimeSnapshotPolicy(payload, intent),
      );
      setPolicy(released);
      form.setFieldValue('reason', '');
      message.success('全局策略已发布，系统正在自动生成并下发设备配置');
    } catch (error) {
      message.error(errorText(error));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="运行快照策略"
      open={open}
      width={760}
      okText="发布全局策略"
      cancelText="关闭"
      confirmLoading={submitting}
      okButtonProps={{ disabled: loading || !policy }}
      onOk={() => void submit()}
      onCancel={() => !submitting && onClose()}
      destroyOnClose
    >
      <Alert
        showIcon
        type="info"
        message="这是诊断快照的空闲兜底周期，不是设备在线心跳"
        description="OneNet 上下线事件仍是在线状态的唯一来源。设备启动、MQTT 重连和实际状态变化会另外及时上报；平台不会清理已有历史数据。"
        style={{ marginBottom: 16 }}
      />

      {policy && (
        <>
          <Descriptions size="small" bordered column={2}>
            <Descriptions.Item label="当前周期">
              {policy.fallbackIntervalMinutes} 分钟
            </Descriptions.Item>
            <Descriptions.Item label="策略版本">
              V{policy.version}
            </Descriptions.Item>
            <Descriptions.Item label="最近修改">
              {policy.updatedBy} · {formatShanghaiTime(policy.updatedAt)}
            </Descriptions.Item>
            <Descriptions.Item label="下发批次">
              <Tag color={policy.rolloutStatus === 'DONE' ? 'success' : 'processing'}>
                {policy.rolloutStatus}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="修改原因" span={2}>
              {policy.changeReason}
            </Descriptions.Item>
          </Descriptions>

          <div style={{ margin: '16px 0' }}>
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Typography.Text strong>自动下发进度</Typography.Text>
              <Typography.Text type="secondary">
                {policy.processedDeviceCount}/{policy.targetDeviceCount}，
                已生成 {policy.publishedDeviceCount} 份配置
              </Typography.Text>
            </Space>
            <Progress percent={progress} status={
              policy.failedDeviceCount + policy.blockedDeviceCount > 0
                ? 'exception'
                : policy.rolloutStatus === 'DONE' ? 'success' : 'active'
            } />
            <Typography.Text type="secondary">
              待应用 {policy.pendingDeviceCount} · 边缘已保存{' '}
              {policy.edgeSavedDeviceCount} · 已应用 {policy.appliedDeviceCount}
              {' '}· 失败 {policy.failedDeviceCount} · 阻塞{' '}
              {policy.blockedDeviceCount}
            </Typography.Text>
          </div>
        </>
      )}

      <Form form={form} layout="vertical" disabled={loading || submitting}>
        <Form.Item
          name="fallbackIntervalMinutes"
          label="空闲兜底周期（分钟）"
          extra="最小 10 分钟；默认 60 分钟。单个设备不能覆盖该值。"
          rules={[
            { required: true, message: '请输入兜底周期' },
            { type: 'integer', min: 10, max: 71582 },
          ]}
        >
          <InputNumber min={10} max={71582} precision={0} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item
          name="reason"
          label="修改原因"
          rules={[
            { required: true, whitespace: true, message: '请填写修改原因' },
            { max: 500 },
          ]}
        >
          <Input.TextArea rows={3} maxLength={500} showCount />
        </Form.Item>
      </Form>
    </Modal>
  );
}
