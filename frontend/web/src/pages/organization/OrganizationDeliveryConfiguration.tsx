import { useCallback, useEffect, useRef, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
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
const MONEY_PATTERN = /^(0|[1-9][0-9]*)\.[0-9]{2}$/;
const WEIGHT_PATTERN = /^(0|[1-9][0-9]*)\.[0-9]{3}$/;

interface OrganizationDeliveryConfigurationProps {
  active: boolean;
  context: DirectoryContext;
  organizationCode: string;
}

type ReviewMode = DeliveryConfigurationVersion['reviewMode'];

const REVIEW_MODE: Record<ReviewMode, { label: string; description: string }> = {
  ALL_MANUAL: {
    label: '全部投递订单人工审核',
    description: '每笔投递都由工作人员审核后才进入钱包',
  },
  NORMAL_AUTO_IMMEDIATE: {
    label: '正常投递订单立即自动审核',
    description: '后端收到正常投递后立即审核；异常订单仍转人工',
  },
  NORMAL_AUTO_AFTER_24H: {
    label: '正常投递订单 24 小时后自动审核',
    description: '从后端收到投递的时间开始等待 24 小时；异常订单仍转人工',
  },
  NORMAL_AUTO_AFTER_48H: {
    label: '正常投递订单 48 小时后自动审核',
    description: '从后端收到投递的时间开始等待 48 小时；异常订单仍转人工',
  },
};

interface DeliveryConfigurationForm {
  reviewMode: ReviewMode;
  automaticReviewMaxAmountYuan?: string;
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
  const [form] = Form.useForm<DeliveryConfigurationForm>();
  const selectedReviewMode = Form.useWatch('reviewMode', form);
  const [current, setCurrent] =
    useState<DeliveryConfigurationVersion | null>(null);
  const [versions, setVersions] =
    useState<DeliveryConfigurationVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const clear = useCallback(() => {
    requestSequence.current += 1;
    setCurrent(null);
    setVersions([]);
    setLoadError(null);
    setLoading(false);
    setSubmitting(false);
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
      form.setFieldsValue({
        reviewMode: loadedCurrent.reviewMode,
        automaticReviewMaxAmountYuan:
          loadedCurrent.automaticReviewMaxAmountYuan ?? undefined,
        openBalanceFloorYuan: loadedCurrent.openBalanceFloorYuan,
        maxReviewAbsoluteWeightKg:
          loadedCurrent.maxReviewAbsoluteWeightKg,
        reason: undefined,
      });
    } catch (error) {
      if (requestSequence.current !== sequence) return;
      setCurrent(null);
      setVersions([]);
      setLoadError(errorMessage(error));
    } finally {
      if (requestSequence.current === sequence) setLoading(false);
    }
  }, [active, context, form, organizationCode]);

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

  const submit = async (values: DeliveryConfigurationForm) => {
    if (!current) return;
    const payload: DeliveryConfigurationReleaseRequest = {
      expectedLatestVersion: current.versionNo,
      reviewMode: values.reviewMode,
      automaticReviewMaxAmountYuan: values.reviewMode === 'ALL_MANUAL'
        ? null
        : values.automaticReviewMaxAmountYuan?.trim() ?? null,
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
      message.success('投递审核规则已保存');
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
        message="页面中的规则可直接修改"
        description="点击保存后，修改只影响之后开始的投递；已开始的会话和订单继续使用当时的规则。系统会自动保留修改记录。"
      />

      <Card
        size="small"
        title="投递审核规则"
      >
        <Form<DeliveryConfigurationForm>
          form={form}
          layout="vertical"
          disabled={submitting}
          onFinish={(values) => void submit(values)}
        >
          <Form.Item
            name="reviewMode"
            label="投递订单审核方式"
            extra="只有重量可靠、金额不为负且没有用户或系统异常的正常订单才会自动审核；其他订单仍进入人工审核。"
            rules={[{ required: true, message: '请选择投递订单审核方式' }]}
          >
            <Select
              options={(Object.entries(REVIEW_MODE) as Array<[
                ReviewMode,
                (typeof REVIEW_MODE)[ReviewMode],
              ]>).map(([value, item]) => ({
                value,
                label: item.label,
              }))}
            />
          </Form.Item>
          {selectedReviewMode && selectedReviewMode !== 'ALL_MANUAL' && (
            <Form.Item
              name="automaticReviewMaxAmountYuan"
              label="投递自动审核单笔结算金额上限（元）"
              extra="原始结算金额小于或等于该值时，正常订单才按上面的时间自动审核；超过后只等待人工审核，不会记为异常。允许填写 0.00。"
              rules={[
                { required: true, message: '请输入自动审核金额上限' },
                {
                  pattern: MONEY_PATTERN,
                  message: '请输入非负金额并精确到分，例如 10.00',
                },
              ]}
            >
              <Input placeholder="10.00" />
            </Form.Item>
          )}
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
            label="修改说明（可选）"
            rules={[{ max: 500, message: '修改说明最多 500 个字符' }]}
          >
            <Input.TextArea
              rows={3}
              placeholder="说明为什么调整本机构规则"
            />
          </Form.Item>
          <Space wrap>
            <Button type="primary" htmlType="submit" loading={submitting}>
              保存投递配置
            </Button>
            <Typography.Text type="secondary">
              上次保存：{formatShanghaiTime(current.publishedAt)}，操作人：{current.publishedBy}
            </Typography.Text>
          </Space>
        </Form>
      </Card>

      <Card size="small" title="修改记录">
        <Table<DeliveryConfigurationVersion>
          size="small"
          rowKey="versionNo"
          pagination={false}
          dataSource={versions}
          scroll={{ x: 760 }}
          columns={[
            {
              title: '保存时间',
              dataIndex: 'publishedAt',
              width: 220,
              render: (value, row) => (
                <Space>
                  <span>{formatShanghaiTime(value)}</span>
                  {row.current && <Tag color="blue">当前</Tag>}
                </Space>
              ),
            },
            {
              title: '投递订单审核方式',
              dataIndex: 'reviewMode',
              width: 220,
              render: (value: ReviewMode) => REVIEW_MODE[value].label,
            },
            {
              title: '投递自动审核金额上限',
              dataIndex: 'automaticReviewMaxAmountYuan',
              width: 150,
              render: (value) => value === null ? '不适用' : `¥ ${value}`,
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
              title: '修改人',
              dataIndex: 'publishedBy',
              width: 130,
            },
          ]}
        />
      </Card>
    </Space>
  );
}
