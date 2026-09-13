import { useCallback, useEffect, useRef, useState } from 'react';
import { PageContainer } from '@ant-design/pro-components';
import { Alert, App, Button, Card, Collapse, Descriptions, Form, Input, InputNumber, Modal, Radio, Space, Spin, Tag, Typography } from 'antd';
import { EditOutlined, ReloadOutlined } from '@ant-design/icons';
import { getDevicePolicy, releaseDevicePolicy, type DevicePolicy } from '@/api/deviceDirectory';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { useAuthStore } from '@/stores/authStore';
import HelpTip from '@/components/HelpTip';
import { pageHeader } from '@/utils/pageStyle';
import { formatShanghaiTime } from '@/utils/decimal';
import { operatorErrorMessage } from '@/pages/device-management/operatorErrorPresentation';

const modeLabels: Record<DevicePolicy['fullnessMode'], string> = {
  WEIGHT_ONLY: '仅重量',
  INFRARED_ONLY: '仅红外',
  INFRARED_OR_WEIGHT: '重量或红外任一满足',
};

interface FormValues {
  unitPriceYuanPerKg: string;
  negativeWeightThresholdGram: number;
  fullnessMode: DevicePolicy['fullnessMode'];
  fullnessWeightKg: string;
  reason: string;
}

export default function DeviceConfigurationPage() {
  const { message } = App.useApp();
  const platform = useAuthStore((state) => state.session?.accountType === 'PLATFORM_ADMIN');
  const [restoring, setRestoring] = useState(false);
  const executeCommand = useCommandExecutor();
  const [form] = Form.useForm<FormValues>();
  const mode = Form.useWatch('fullnessMode', form);
  const [policy, setPolicy] = useState<DevicePolicy>();
  // Keep the version the operator actually reviewed, even while progress polls.
  const [editing, setEditing] = useState<DevicePolicy>();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [issue, setIssue] = useState<string>();
  const sequence = useRef(0);
  const submittingRef = useRef(false);

  const refresh = useCallback(async (quiet = false) => {
    if (submittingRef.current) return;
    const request = ++sequence.current;
    if (!quiet) setLoading(true);
    try {
      const result = await getDevicePolicy(platform);
      if (request !== sequence.current) return;
      setPolicy(result);
      setIssue(undefined);
    } catch (error) {
      if (request === sequence.current) {
        setIssue(operatorErrorMessage(error, '设备配置读取失败，请重试'));
      }
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }, [platform]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh(true);
    }, 5000);
    return () => { window.clearInterval(timer); sequence.current += 1; };
  }, [refresh]);

  const edit = (restore = false) => {
    if (!policy) return;
    const values = restore ? policy.platformDefaults : policy;
    form.setFieldsValue({ ...values, reason: '' });
    setRestoring(restore);
    setEditing(policy);
  };

  const publish = async (values: FormValues) => {
    if (!editing || submittingRef.current) return;
    const payload = {
      expectedVersion: editing.version,
      expectedDefaultVersion: editing.defaultVersion,
      configurationMode: (platform ? 'DEFAULT' : restoring ? 'INHERIT' : 'CUSTOM') as DevicePolicy['configurationMode'],
      unitPriceYuanPerKg: values.unitPriceYuanPerKg,
      negativeWeightThresholdGram: values.negativeWeightThresholdGram,
      fullnessMode: values.fullnessMode,
      fullnessWeightKg: values.fullnessWeightKg,
      reason: values.reason.trim(),
    };
    submittingRef.current = true;
    sequence.current += 1;
    setLoading(false);
    setSubmitting(true);
    try {
      const released = await executeCommand(
        commandKey('device.configuration-policy.release', platform ? 'DEFAULT' : 'TENANT', payload),
        (intent) => releaseDevicePolicy(platform, payload, intent),
      );
      setPolicy(released);
      setIssue(undefined);
      setEditing(undefined);
      message.success('设备配置已发布，等待各设备应用');
    } catch (error) {
      message.error(operatorErrorMessage(error, '设备配置发布失败，请重试'));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <PageContainer header={pageHeader('设备配置')}>
      <Space direction="vertical" size={16} style={{ width: '100%', maxWidth: 960 }}>
        {issue && <Alert type="warning" showIcon message={issue} action={<Button size="small" onClick={() => void refresh()}>重试</Button>} />}
        <Card title={<>{platform ? '平台默认配置' : '租户统一配置'} <HelpTip label="设备配置适用范围">{platform ? '适用于使用平台默认值的租户；租户自行设置后不受平台后续修改影响。' : '统一适用于本租户所有机构、设备和投口。空袋重量仍按每个投口实际测量。'}</HelpTip></>} extra={
          <Space>
            <Button icon={<ReloadOutlined />} aria-label="刷新设备配置" loading={loading} onClick={() => void refresh()} />
            {!platform && policy?.configurationMode === 'CUSTOM' && <Button disabled={loading || !!issue} onClick={() => edit(true)}>恢复平台默认</Button>}
            <Button type="primary" icon={<EditOutlined />} aria-label="修改设置" disabled={!policy || loading || !!issue} onClick={() => edit()}>修改设置</Button>
          </Space>
        }>
          {!policy ? <Spin spinning={loading}><Typography.Text type="secondary">正在读取设置</Typography.Text></Spin> : (
            <>
            {!platform && <Tag color={policy.configurationMode === 'CUSTOM' ? 'blue' : 'default'} style={{ marginBottom: 16 }}>{policy.configurationMode === 'CUSTOM' ? '使用租户设置' : '使用平台默认'}</Tag>}
            <Descriptions column={2} size="middle">
              <Descriptions.Item label="单价">{policy.unitPriceYuanPerKg} 元/千克</Descriptions.Item>
              <Descriptions.Item label="判断方式">{modeLabels[policy.fullnessMode]}</Descriptions.Item>
              {policy.fullnessMode !== 'INFRARED_ONLY' && <Descriptions.Item label="满溢净重">{policy.fullnessWeightKg} 千克</Descriptions.Item>}
              <Descriptions.Item label="最近修改">{policy.updatedBy} · {formatShanghaiTime(policy.updatedAt)}</Descriptions.Item>

            </Descriptions>
            <Collapse ghost items={[{ key: 'advanced', label: '更多设置', children: <Typography.Text>重量减少异常阈值：{policy.negativeWeightThresholdGram} 克 <HelpTip label="重量减少异常阈值说明">一次投递中，任一轮称重减少达到此值，会记录异常并转人工审核。</HelpTip></Typography.Text> }]} />
            </>
          )}
        </Card>
        {policy && <Card title={<>设备应用进度 <HelpTip label="设备应用进度说明">仅统计已分配机构、验收通过且未停用的设备。离线设备等待联网；停用设备恢复后同步最新设置。已应用以设备确认结果为准。</HelpTip></>} extra={
          <Tag color={policy.rolloutStatus === 'DONE' ? 'default' : 'processing'}>
            {policy.rolloutStatus === 'DONE' ? '配置已生成' : '正在生成配置'}
          </Tag>
        }>
          <Space size={[24, 12]} wrap>
            <Typography.Text>已应用 <Typography.Text strong>{policy.appliedDeviceCount}</Typography.Text></Typography.Text>
            <Typography.Text>待应用 {policy.pendingDeviceCount}</Typography.Text>
            <Typography.Text>设备已保存 {policy.edgeSavedDeviceCount}</Typography.Text>
            <Typography.Text type={policy.failedDeviceCount ? 'danger' : 'secondary'}>失败 {policy.failedDeviceCount}</Typography.Text>
            <Typography.Text type={policy.blockedDeviceCount ? 'danger' : 'secondary'}>阻塞 {policy.blockedDeviceCount}</Typography.Text>
          </Space>
          {(policy.failedDeviceCount > 0 || policy.blockedDeviceCount > 0) && <Alert type="warning" showIcon style={{ marginTop: 16 }} message="部分设备尚未应用，请在设备详情中查看失败原因并处理。" />}
        </Card>}
      </Space>
      <Modal title={restoring ? '恢复平台默认' : '修改设备配置'} open={!!editing} onCancel={() => !submitting && setEditing(undefined)}
        okText={restoring ? '恢复并发布' : platform ? '发布默认配置' : '发布到本租户设备'} cancelText="取消" confirmLoading={submitting}
        onOk={() => form.submit()} destroyOnClose>
        <Alert type="warning" showIcon message={platform ? '影响使用平台默认值的租户。设备确认前可能暂停新投递；已有订单及进行中的投递、清运保留原单价和配置。' : '影响本租户所有设备。设备确认前可能暂停新投递；已有订单及进行中的投递、清运保留原单价和配置。'} style={{ marginBottom: 20 }} />
        <Form form={form} layout="vertical" onFinish={publish} disabled={submitting}>
          {restoring && editing && <Descriptions column={1} size="small" style={{ marginBottom: 20 }}>
            <Descriptions.Item label="平台单价">{editing.platformDefaults.unitPriceYuanPerKg} 元/千克</Descriptions.Item>
            <Descriptions.Item label="判断方式">{modeLabels[editing.platformDefaults.fullnessMode]}</Descriptions.Item>
            {editing.platformDefaults.fullnessMode !== 'INFRARED_ONLY' && <Descriptions.Item label="满溢净重">{editing.platformDefaults.fullnessWeightKg} 千克</Descriptions.Item>}
            <Descriptions.Item label="重量减少异常阈值">{editing.platformDefaults.negativeWeightThresholdGram} 克</Descriptions.Item>
            <Descriptions.Item label="后续修改">继续跟随平台默认值</Descriptions.Item>
          </Descriptions>}
          <Form.Item hidden={restoring} name="unitPriceYuanPerKg" label="单价（元/千克）" rules={[{ required: true, message: '请输入单价' }, { validator: async (_, value: string) => {
            if (!value || !/^\d{1,6}(\.\d{1,4})?$/.test(value) || Number(value) <= 0 || Number(value) > 429496.7295) throw new Error('请输入 0.0001～429496.7295，最多四位小数');
          } }]}>
            <InputNumber stringMode min="0.0001" max="429496.7295" precision={4} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item hidden={restoring} name="fullnessMode" label="判断方式" rules={[{ required: true }]}>
            <Radio.Group options={Object.entries(modeLabels).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="fullnessWeightKg" hidden={restoring || mode === 'INFRARED_ONLY'}
            label="满溢净重（千克）" tooltip="桶内总重量扣除实际空袋重量，达到此值判定重量满溢。调整规则不会直接改写已有满溢结果，仍需设备重新检测。"
            rules={[{ required: true, message: '请输入满溢净重' }, { validator: async (_, value: string) => {
              if (!value || !/^\d{1,7}(\.\d{1,3})?$/.test(value) || Number(value) <= 0 || Number(value) > 4294967.295) {
                throw new Error('请输入 0.001～4294967.295 千克，最多三位小数');
              }
            } }]}>
            <InputNumber stringMode min="0.001" max="4294967.295" precision={3} style={{ width: '100%' }} />
          </Form.Item>
          {!restoring && <Collapse ghost style={{ marginBottom: 16 }} items={[{ key: 'advanced', label: '更多设置', forceRender: true, children:
            <Form.Item name="negativeWeightThresholdGram" label="重量减少异常阈值（克）" tooltip="一次投递中，任一轮称重减少达到此值，会记录异常并转人工审核。" rules={[{ required: true, message: '请输入异常阈值' }, { type: 'integer', min: 1, max: 4294967295, message: '请输入 1～4294967295 的整数' }]}>
              <InputNumber min={1} max={4294967295} precision={0} style={{ width: '100%' }} />
            </Form.Item>
          }]} />}
          <Form.Item name="reason" label="修改原因" rules={[{ required: true, whitespace: true, message: '请填写修改原因' }]}>
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
