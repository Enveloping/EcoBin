import { useEffect } from 'react';
import {
  Alert,
  Col,
  Collapse,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Switch,
  Typography,
} from 'antd';
import type {
  DeviceConfigurationReleaseRequest,
  DeviceConfigurationVersion,
} from '@/api/deviceDirectory';

interface ConfigurationFormValues
  extends Omit<DeviceConfigurationReleaseRequest, 'expectedLatestVersion'> {}

interface DeviceConfigurationModalProps {
  open: boolean;
  submitting: boolean;
  portCount: number;
  latest?: DeviceConfigurationVersion;
  onCancel: () => void;
  onSubmit: (request: DeviceConfigurationReleaseRequest) => Promise<void>;
}

const PRICE_PATTERN = /^(0|[1-9][0-9]*)\.[0-9]{4}$/;
const WEIGHT_PATTERN = /^(0|[1-9][0-9]*)\.[0-9]{2}$/;

function optionalText(value: string | null | undefined): string | null {
  const normalized = value?.trim();
  return normalized ? normalized : null;
}

function initialValues(
  portCount: number,
  latest?: DeviceConfigurationVersion,
): ConfigurationFormValues {
  if (latest) {
    return {
      reason: null,
      device: {
        mcuHeartbeatIntervalMs: latest.device.mcuHeartbeatIntervalMs,
        mcuHeartbeatMissThreshold:
          latest.device.mcuHeartbeatMissThreshold,
        doorCloseRetryLimit: latest.device.doorCloseRetryLimit,
        continueDeliveryWaitMs: latest.device.continueDeliveryWaitMs,
        negativeWeightThresholdGram:
          latest.device.negativeWeightThresholdGram,
        deliveryAutoCloseMs: latest.device.deliveryAutoCloseMs,
        weightMeasurementTimeoutMs:
          latest.device.weightMeasurementTimeoutMs,
        deliveryDoorTravelWaitMs:
          latest.device.deliveryDoorTravelWaitMs,
        cleanSolenoidPulseMs: latest.device.cleanSolenoidPulseMs,
        smokeMonitoringEnabled: latest.device.smokeMonitoringEnabled,
      },
      ports: latest.ports.map((port) => ({ ...port })),
    };
  }
  return {
    reason: null,
    device: {
      mcuHeartbeatIntervalMs: 5000,
      mcuHeartbeatMissThreshold: 3,
      doorCloseRetryLimit: 3,
      continueDeliveryWaitMs: 30000,
      negativeWeightThresholdGram: 500,
    },
    ports: Array.from({ length: portCount }, (_, index) => ({
      portNo: index + 1,
      displayName: `${index + 1} 号投口`,
      enabled: true,
      unitPriceYuanPerKg: '0.8000',
      fullnessMode: 'INFRARED_OR_WEIGHT' as const,
      fullnessWeightKg: '50.00',
      deliverySettleDelayMs: 3000,
      fullnessInitialDelayMs: 3000,
      fullnessRecheckDelayMs: 10000,
      doorAutoCloseTimeoutMs: 60000,
    })),
  };
}

const positiveIntegerRule = {
  type: 'integer' as const,
  min: 1,
  message: '请输入大于 0 的整数',
};

const nonNegativeIntegerRule = {
  type: 'integer' as const,
  min: 0,
  message: '请输入不小于 0 的整数',
};

export default function DeviceConfigurationModal({
  open,
  submitting,
  portCount,
  latest,
  onCancel,
  onSubmit,
}: DeviceConfigurationModalProps) {
  const [form] = Form.useForm<ConfigurationFormValues>();

  useEffect(() => {
    if (open) {
      form.setFieldsValue(initialValues(portCount, latest));
    }
  }, [form, latest, open, portCount]);

  const submit = async () => {
    const values = await form.validateFields();
    await onSubmit({
      expectedLatestVersion: latest?.versionNo ?? 0,
      reason: optionalText(values.reason),
      device: { ...values.device },
      ports: values.ports.map((port, index) => ({
        ...latest?.ports[index],
        ...port,
        portNo: index + 1,
      })),
    });
  };

  return (
    <Modal
      title={latest ? `发布设备配置 v${latest.versionNo + 1}` : '发布首版设备配置'}
      open={open}
      width={1040}
      confirmLoading={submitting}
      okText="发布并下发"
      cancelText="取消"
      onOk={() => void submit()}
      onCancel={() => !submitting && onCancel()}
      destroyOnClose
    >
      <Alert
        showIcon
        type="warning"
        message="发布只表示中心已经可靠受理配置意图"
        description="配置必须由香橙派可靠保存并返回精确证明后才算已应用。发布期间不会取消已经开始的投递或清运，新作业会等待最新配置应用完成。"
        style={{ marginBottom: 16 }}
      />
      <Form<ConfigurationFormValues>
        form={form}
        layout="vertical"
        disabled={submitting}
      >
        <Typography.Title level={5}>整机安全时序</Typography.Title>
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item
              name={['device', 'mcuHeartbeatIntervalMs']}
              label="设备控制板心跳间隔（毫秒）"
              rules={[{ required: true }, positiveIntegerRule]}
            >
              <InputNumber style={{ width: '100%' }} precision={0} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item
              name={['device', 'mcuHeartbeatMissThreshold']}
              label="设备控制板心跳丢失阈值"
              rules={[{ required: true }, positiveIntegerRule]}
            >
              <InputNumber style={{ width: '100%' }} precision={0} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item
              name={['device', 'doorCloseRetryLimit']}
              label="关门重试次数"
              rules={[{ required: true }, nonNegativeIntegerRule]}
            >
              <InputNumber style={{ width: '100%' }} precision={0} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item
              name={['device', 'continueDeliveryWaitMs']}
              label="继续投递等待窗口（ms）"
              extra="用户不选择时，默认在窗口届满后结束整场投递。"
              rules={[{ required: true }, positiveIntegerRule]}
            >
              <InputNumber style={{ width: '100%' }} precision={0} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item
              name={['device', 'negativeWeightThresholdGram']}
              label="负重量异常阈值（g）"
              extra="任一本地轮次减少达到该值时，只锁存异常标志。"
              rules={[{ required: true }, positiveIntegerRule]}
            >
              <InputNumber style={{ width: '100%' }} precision={0} />
            </Form.Item>
          </Col>
        </Row>

        <Collapse
          ghost
          items={[{
            key: 'device-advanced',
            label: '整机高级时序与烟雾监测',
            children: (
              <Row gutter={16}>
                {[
                  ['deliveryAutoCloseMs', '投递门自动关闭（ms）'],
                  ['weightMeasurementTimeoutMs', '整机称重超时（ms）'],
                  ['deliveryDoorTravelWaitMs', '投递门行程等待（ms）'],
                  ['cleanSolenoidPulseMs', '清运电磁阀脉冲（ms）'],
                ].map(([name, label]) => (
                  <Col span={6} key={name}>
                    <Form.Item
                      name={['device', name]}
                      label={label}
                      rules={[positiveIntegerRule]}
                    >
                      <InputNumber
                        style={{ width: '100%' }}
                        precision={0}
                        placeholder="未配置"
                      />
                    </Form.Item>
                  </Col>
                ))}
                <Col span={6}>
                  <Form.Item
                    name={['device', 'smokeMonitoringEnabled']}
                    label="烟雾监测"
                    valuePropName="checked"
                  >
                    <Switch checkedChildren="启用" unCheckedChildren="停用" />
                  </Form.Item>
                </Col>
              </Row>
            ),
          }]}
        />

        <Typography.Title level={5} style={{ marginTop: 8 }}>
          全部投口配置
        </Typography.Title>
        <Alert
          showIcon
          type="info"
          message={`必须完整配置 1 至 ${portCount} 号投口；停用投口请关闭开关，单价不能填写为 0。`}
          style={{ marginBottom: 12 }}
        />
        <Collapse
          defaultActiveKey={['port-0']}
          items={Array.from({ length: portCount }, (_, index) => ({
            key: `port-${index}`,
            label: `${index + 1} 号投口`,
            children: (
              <>
                <Row gutter={16}>
                  <Col span={6}>
                    <Form.Item
                      name={['ports', index, 'displayName']}
                      label="投口名称"
                      rules={[
                        { required: true, message: '请输入投口名称' },
                        { max: 32, message: '最多 32 个字符' },
                      ]}
                    >
                      <Input />
                    </Form.Item>
                  </Col>
                  <Col span={4}>
                    <Form.Item
                      name={['ports', index, 'enabled']}
                      label="允许投递"
                      valuePropName="checked"
                    >
                      <Switch />
                    </Form.Item>
                  </Col>
                  <Col span={6}>
                    <Form.Item
                      name={['ports', index, 'unitPriceYuanPerKg']}
                      label="回收单价（元/kg）"
                      rules={[
                        { required: true, message: '请输入单价' },
                        {
                          pattern: PRICE_PATTERN,
                          message: '保留四位小数，例如 0.8000',
                        },
                        {
                          validator: (_, value) => Number(value) > 0
                            ? Promise.resolve()
                            : Promise.reject(new Error('单价必须大于 0')),
                        },
                      ]}
                    >
                      <Input placeholder="0.8000" />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item
                      name={['ports', index, 'fullnessMode']}
                      label="满溢判断方式"
                      rules={[{ required: true }]}
                    >
                      <Select options={[
                        { label: '红外', value: 'INFRARED_ONLY' },
                        { label: '重量', value: 'WEIGHT_ONLY' },
                        { label: '红外或重量', value: 'INFRARED_OR_WEIGHT' },
                      ]} />
                    </Form.Item>
                  </Col>
                  <Col span={6}>
                    <Form.Item
                      name={['ports', index, 'fullnessWeightKg']}
                      label="满溢重量阈值（kg）"
                      rules={[
                        { required: true, message: '请输入重量阈值' },
                        {
                          pattern: WEIGHT_PATTERN,
                          message: '保留两位小数，例如 50.00',
                        },
                        {
                          validator: (_, value) => Number(value) > 0
                            ? Promise.resolve()
                            : Promise.reject(new Error('阈值必须大于 0')),
                        },
                      ]}
                    >
                      <Input placeholder="50.00" />
                    </Form.Item>
                  </Col>
                  {[
                    ['deliverySettleDelayMs', '投递稳定等待（ms）', 0],
                    ['fullnessInitialDelayMs', '满溢初次等待（ms）', 0],
                    ['fullnessRecheckDelayMs', '满溢复检等待（ms）', 0],
                    ['doorAutoCloseTimeoutMs', '关门超时（ms）', 1000],
                  ].map(([name, label, min]) => (
                    <Col span={6} key={String(name)}>
                      <Form.Item
                        name={['ports', index, name]}
                        label={String(label)}
                        rules={[
                          { required: true },
                          {
                            type: 'integer',
                            min: Number(min),
                            message: `必须是不小于 ${min} 的整数`,
                          },
                        ]}
                      >
                        <InputNumber style={{ width: '100%' }} precision={0} />
                      </Form.Item>
                    </Col>
                  ))}
                </Row>
                <Collapse
                  ghost
                  items={[{
                    key: 'sensor',
                    label: '传感器与称重高级参数',
                    children: (
                      <Row gutter={16}>
                        <Col span={6}>
                          <Form.Item
                            name={['ports', index, 'fullnessSensorKind']}
                            label="满溢传感器"
                          >
                            <Select
                              allowClear
                              options={[
                                { label: '超声波', value: 'ULTRASONIC' },
                                { label: '数字红外', value: 'DIGITAL_INFRARED' },
                              ]}
                            />
                          </Form.Item>
                        </Col>
                        {([
                          ['fullnessDistanceThresholdMm', '距离阈值（mm）', 1],
                          ['fullnessSampleCount', '满溢采样数', 1],
                          ['fullnessMinimumValidSampleCount', '最少有效采样数', 1],
                          ['fullnessEchoTimeoutUs', '回波超时（μs）', 1],
                          ['weightStableWindowMs', '称重稳定窗口（ms）', 1],
                          ['weightMaximumFluctuationGram', '最大波动（g）', 0],
                          ['weightRequiredSampleCount', '称重采样数', 1],
                          ['weightMeasurementTimeoutMs', '称重超时（ms）', 1],
                          ['weightMinimumGram', '称重下限（g）', undefined],
                          ['weightMaximumGram', '称重上限（g）', undefined],
                          ['calibrationVersion', '标定版本', 0],
                          ['infraredSampleTimeoutMs', '红外采样超时（ms）', 1],
                          ['deliveryDoorOperationTimeoutMs', '投递门操作超时（ms）', 1],
                        ] as Array<[string, string, number | undefined]>).map(([
                          name,
                          label,
                          min,
                        ]) => (
                          <Col span={6} key={String(name)}>
                            <Form.Item
                              name={['ports', index, name]}
                              label={String(label)}
                              rules={min === undefined ? [] : [{
                                type: 'integer',
                                min: Number(min),
                                message: `必须是不小于 ${min} 的整数`,
                              }]}
                            >
                              <InputNumber
                                style={{ width: '100%' }}
                                precision={0}
                                placeholder="未配置"
                              />
                            </Form.Item>
                          </Col>
                        ))}
                      </Row>
                    ),
                  }]}
                />
              </>
            ),
          }))}
        />

        <Form.Item
          name="reason"
          label="发布原因"
          rules={[{ max: 500, message: '最多 500 个字符' }]}
          style={{ marginTop: 16 }}
        >
          <Input.TextArea
            rows={3}
            placeholder="说明这次配置发布或调整的原因"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
