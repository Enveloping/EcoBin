import { useCallback, useEffect, useRef, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Modal,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  getDeliveryConfiguration,
  listDeliveryConfigurationVersions,
  releaseDeliveryConfiguration,
  type DeliveryConfigurationReleaseRequest,
  type DeliveryConfigurationVersion,
} from '@/api/deliveryConfiguration';
import type { DirectoryContext } from '@/api/identityDirectory';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';

const FLOOR_PATTERN = /^-(0|[1-9][0-9]*)\.[0-9]{2}$/;
const WEIGHT_PATTERN = /^(0|[1-9][0-9]*)\.[0-9]{3}$/;

interface OrganizationDeliveryConfigurationProps {
  active: boolean;
  context: DirectoryContext;
  organizationCode: string;
}

interface ReleaseForm {
  reviewMode: 'ALL_MANUAL';
  openBalanceFloorYuan: string;
  maxReviewAbsoluteWeightKg: string;
  reason?: string;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '投递规则加载失败';
}

export default function OrganizationDeliveryConfiguration({
  active,
  context,
  organizationCode,
}: OrganizationDeliveryConfigurationProps) {
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const requestSequence = useRef(0);
  const [form] = Form.useForm<ReleaseForm>();
  const [current, setCurrent] =
    useState<DeliveryConfigurationVersion | null>(null);
  const [versions, setVersions] =
    useState<DeliveryConfigurationVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const clear = useCallback(() => {
    requestSequence.current += 1;
    setCurrent(null);
    setVersions([]);
    setLoadError(null);
    setLoading(false);
    setSubmitting(false);
    setReleaseOpen(false);
    form.resetFields();
  }, [form]);

  const load = useCallback(async () => {
    if (!active) return;
    const sequence = ++requestSequence.current;
    setLoading(true);
    setLoadError(null);
    try {
      const [loadedCurrent, history] = await Promise.all([
        getDeliveryConfiguration(context, organizationCode),
        listDeliveryConfigurationVersions(context, organizationCode, {
          limit: 100,
        }),
      ]);
      if (requestSequence.current !== sequence) return;
      setCurrent(loadedCurrent);
      setVersions(history.items);
    } catch (error) {
      if (requestSequence.current !== sequence) return;
      setCurrent(null);
      setVersions([]);
      setLoadError(errorMessage(error));
    } finally {
      if (requestSequence.current === sequence) setLoading(false);
    }
  }, [active, context, organizationCode]);

  useEffect(() => {
    if (active) {
      void load();
      return () => {
        requestSequence.current += 1;
      };
    }
    clear();
    return undefined;
  }, [active, clear, load]);

  const openRelease = () => {
    if (!current) return;
    form.setFieldsValue({
      reviewMode: 'ALL_MANUAL',
      openBalanceFloorYuan: current.openBalanceFloorYuan,
      maxReviewAbsoluteWeightKg: current.maxReviewAbsoluteWeightKg,
      reason: undefined,
    });
    setReleaseOpen(true);
  };

  const submit = async () => {
    if (!current) return;
    let values: ReleaseForm;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const payload: DeliveryConfigurationReleaseRequest = {
      expectedLatestVersion: current.versionNo,
      reviewMode: 'ALL_MANUAL',
      openBalanceFloorYuan: values.openBalanceFloorYuan.trim(),
      maxReviewAbsoluteWeightKg:
        values.maxReviewAbsoluteWeightKg.trim(),
      reason: values.reason?.trim() || null,
    };

    setSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'release-delivery-configuration',
          organizationCode,
          payload,
        ),
        (intent) => releaseDeliveryConfiguration(
          context,
          organizationCode,
          payload,
          intent,
        ),
      );
      message.success('机构投递规则已发布');
      setReleaseOpen(false);
      await load();
    } catch (error) {
      message.error(errorMessage(error));
      if (error instanceof ApiProblem && error.isVersionConflict) {
        await load();
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (loading || (!current && !loadError)) {
    return <Skeleton active paragraph={{ rows: 7 }} />;
  }

  if (loadError) {
    return (
      <Alert
        showIcon
        type="error"
        message="暂时无法读取投递规则"
        description={loadError}
        action={(
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => void load()}
          >
            重试
          </Button>
        )}
      />
    );
  }

  if (!current) {
    return <Skeleton active paragraph={{ rows: 7 }} />;
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        showIcon
        type="info"
        message="投递规则按版本发布"
        description="新版本只影响之后开始的投递；已开始的会话和订单继续使用当时冻结的规则。"
      />

      <Card
        size="small"
        title={(
          <Space>
            <span>当前投递与审核规则</span>
            <Tag color="blue">v{current.versionNo}</Tag>
          </Space>
        )}
        extra={(
          <Button type="primary" onClick={openRelease}>
            发布新版本
          </Button>
        )}
      >
        <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
          <Descriptions.Item label="审核方式">
            <Space direction="vertical" size={0}>
              <Typography.Text>全部人工审核</Typography.Text>
              <Typography.Text type="secondary">
                订单须经人工认定后才改变钱包余额
              </Typography.Text>
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="负余额停投下限">
            ¥ {current.openBalanceFloorYuan}
          </Descriptions.Item>
          <Descriptions.Item label="人工认定重量上限">
            ±{current.maxReviewAbsoluteWeightKg} kg
          </Descriptions.Item>
          <Descriptions.Item label="发布时间">
            {formatShanghaiTime(current.publishedAt)}
          </Descriptions.Item>
          <Descriptions.Item label="发布人">
            {current.publishedBy}
          </Descriptions.Item>
          <Descriptions.Item label="配置摘要">
            <Typography.Text copyable ellipsis>
              {current.contentSha256}
            </Typography.Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card size="small" title="历史版本">
        <Table<DeliveryConfigurationVersion>
          size="small"
          rowKey="versionNo"
          pagination={false}
          dataSource={versions}
          scroll={{ x: 760 }}
          columns={[
            {
              title: '版本',
              dataIndex: 'versionNo',
              width: 100,
              render: (value, row) => (
                <Space>
                  <span>v{value}</span>
                  {row.current && <Tag color="blue">当前</Tag>}
                </Space>
              ),
            },
            {
              title: '审核方式',
              dataIndex: 'reviewMode',
              width: 120,
              render: () => '全部人工审核',
            },
            {
              title: '停投下限',
              dataIndex: 'openBalanceFloorYuan',
              width: 120,
              render: (value) => `¥ ${value}`,
            },
            {
              title: '认定上限',
              dataIndex: 'maxReviewAbsoluteWeightKg',
              width: 130,
              render: (value) => `±${value} kg`,
            },
            {
              title: '发布人',
              dataIndex: 'publishedBy',
              width: 130,
            },
            {
              title: '发布时间',
              dataIndex: 'publishedAt',
              width: 170,
              render: (value) => formatShanghaiTime(value),
            },
          ]}
        />
      </Card>

      <Modal
        title="发布新的投递与审核规则"
        open={releaseOpen}
        width={620}
        confirmLoading={submitting}
        okText="确认发布"
        cancelText="取消"
        onOk={() => void submit()}
        onCancel={() => !submitting && setReleaseOpen(false)}
        destroyOnClose
      >
        <Alert
          showIcon
          type="warning"
          message="发布后不可覆盖或删除"
          description="如需再次调整，必须在当前版本之上发布另一个新版本。"
          style={{ marginBottom: 16 }}
        />
        <Form<ReleaseForm>
          form={form}
          layout="vertical"
          disabled={submitting}
        >
          <Form.Item
            name="reviewMode"
            label="审核方式"
            extra="当前阶段固定全部人工审核；自动审核尚未开放。"
          >
            <Select
              options={[{ label: '全部人工审核', value: 'ALL_MANUAL' }]}
              disabled
            />
          </Form.Item>
          <Form.Item
            name="openBalanceFloorYuan"
            label="负余额停投下限（元）"
            extra="用户可提现余额达到或低于这个值时，不能开始下一次投递。"
            rules={[
              { required: true, message: '请输入负余额停投下限' },
              {
                pattern: FLOOR_PATTERN,
                message: '请输入负数并精确到分，例如 -10.00',
              },
              {
                validator: (_, value) => value === '-0.00'
                  ? Promise.reject(new Error('停投下限必须小于 0 元'))
                  : Promise.resolve(),
              },
            ]}
          >
            <Input placeholder="-10.00" />
          </Form.Item>
          <Form.Item
            name="maxReviewAbsoluteWeightKg"
            label="人工认定重量绝对值上限（kg）"
            extra="防止审核人员误输入极端重量，允许范围为 0.001 至 1000.000 kg。"
            rules={[
              { required: true, message: '请输入人工认定重量上限' },
              {
                pattern: WEIGHT_PATTERN,
                message: '请精确到克，例如 100.000',
              },
              {
                validator: (_, value) => {
                  const parsed = Number(value);
                  return parsed >= 0.001 && parsed <= 1000
                    ? Promise.resolve()
                    : Promise.reject(
                      new Error('重量必须在 0.001 至 1000.000 kg 之间'),
                    );
                },
              },
            ]}
          >
            <Input placeholder="100.000" />
          </Form.Item>
          <Form.Item
            name="reason"
            label="发布原因"
            rules={[{ max: 500, message: '发布原因最多 500 个字符' }]}
          >
            <Input.TextArea
              rows={3}
              placeholder="说明为什么调整本机构规则"
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
