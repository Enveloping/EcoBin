import { useCallback, useEffect, useState } from 'react';
import { PageContainer } from '@ant-design/pro-components';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
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
} from 'antd';
import {
  getDeliveryConfiguration,
  listDeliveryConfigurationVersions,
  releaseDeliveryConfiguration,
  type DeliveryConfigurationReleaseRequest,
  type DeliveryConfigurationVersion,
} from '@/api/deliveryConfiguration';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { ApiProblem } from '@/api/request';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader } from '@/utils/pageStyle';

const FLOOR_PATTERN = /^-(0|[1-9][0-9]*)\.[0-9]{2}$/;
const WEIGHT_PATTERN = /^(0|[1-9][0-9]*)\.[0-9]{3}$/;

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

export default function DeliveryConfigurationPage() {
  const directoryScope = useDirectoryScope();
  const organizationScope = useOrganizationScope(directoryScope);
  const executeCommand = useCommandExecutor();
  const { message } = App.useApp();
  const [form] = Form.useForm<ReleaseForm>();
  const [current, setCurrent] =
    useState<DeliveryConfigurationVersion | null>(null);
  const [versions, setVersions] =
    useState<DeliveryConfigurationVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [loadError, setLoadError] = useState<string>();

  const load = useCallback(async () => {
    if (
      !directoryScope.context
      || !organizationScope.organizationCode
    ) {
      setCurrent(null);
      setVersions([]);
      return;
    }
    setLoading(true);
    setLoadError(undefined);
    try {
      const [loadedCurrent, history] = await Promise.all([
        getDeliveryConfiguration(
          directoryScope.context,
          organizationScope.organizationCode,
        ),
        listDeliveryConfigurationVersions(
          directoryScope.context,
          organizationScope.organizationCode,
          { limit: 100 },
        ),
      ]);
      setCurrent(loadedCurrent);
      setVersions(history.items);
    } catch (error) {
      setCurrent(null);
      setVersions([]);
      setLoadError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [
    directoryScope.context,
    organizationScope.organizationCode,
  ]);

  useEffect(() => {
    void load();
  }, [load]);

  const openRelease = () => {
    if (!current) return;
    form.setFieldsValue({
      reviewMode: 'ALL_MANUAL',
      openBalanceFloorYuan: current.openBalanceFloorYuan,
      maxReviewAbsoluteWeightKg:
        current.maxReviewAbsoluteWeightKg,
      reason: undefined,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    if (
      !current
      || !directoryScope.context
      || !organizationScope.organizationCode
    ) {
      return;
    }
    const values = await form.validateFields();
    const payload: DeliveryConfigurationReleaseRequest = {
      expectedLatestVersion: current.versionNo,
      reviewMode: 'ALL_MANUAL',
      openBalanceFloorYuan:
        values.openBalanceFloorYuan.trim(),
      maxReviewAbsoluteWeightKg:
        values.maxReviewAbsoluteWeightKg.trim(),
      reason: values.reason?.trim() || null,
    };
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'release-delivery-configuration',
          organizationScope.organizationCode,
          payload,
        ),
        (intent) => releaseDeliveryConfiguration(
          directoryScope.context!,
          organizationScope.organizationCode!,
          payload,
          intent,
        ),
      );
      message.success('机构投递规则已发布');
      setModalOpen(false);
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

  const scopeReady =
    !!directoryScope.context
    && !!organizationScope.organizationCode;

  const content = (() => {
    if (directoryScope.loading || organizationScope.loading) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在加载机构范围" />
        </div>
      );
    }
    if (!directoryScope.context) {
      return <Empty description="请选择目标租户" />;
    }
    if (!organizationScope.organizationOptions.length) {
      return <Empty description="当前租户尚无机构" />;
    }
    if (!organizationScope.organizationCode) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在应用机构范围" />
        </div>
      );
    }
    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card>
          <Space wrap>
            <Typography.Text strong>目标机构</Typography.Text>
            <Select
              aria-label="目标机构"
              showSearch
              optionFilterProp="label"
              style={{ width: 360 }}
              value={organizationScope.organizationCode}
              options={organizationScope.organizationOptions}
              onChange={organizationScope.setOrganizationCode}
            />
          </Space>
        </Card>

        {loadError && (
          <Alert
            showIcon
            type="error"
            message="投递规则加载失败"
            description={loadError}
            action={<Button onClick={() => void load()}>重试</Button>}
          />
        )}

        {loading ? (
          <Card>
            <div style={{ padding: 40, textAlign: 'center' }}>
              <Spin tip="正在读取当前投递规则" />
            </div>
          </Card>
        ) : current ? (
          <>
            <Card
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
              <Descriptions column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="审核方式">
                  <Space direction="vertical" size={0}>
                    <Typography.Text>全部人工审核</Typography.Text>
                    <Typography.Text type="secondary">
                      每笔订单都必须人工认定后才改变钱包余额
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

            <Card title="历史版本">
              <Table<DeliveryConfigurationVersion>
                rowKey="versionNo"
                pagination={false}
                dataSource={versions}
                columns={[
                  {
                    title: '版本',
                    dataIndex: 'versionNo',
                    width: 90,
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
                    render: () => '全部人工审核',
                  },
                  {
                    title: '停投下限',
                    dataIndex: 'openBalanceFloorYuan',
                    render: (value) => `¥ ${value}`,
                  },
                  {
                    title: '认定上限',
                    dataIndex: 'maxReviewAbsoluteWeightKg',
                    render: (value) => `±${value} kg`,
                  },
                  {
                    title: '发布人',
                    dataIndex: 'publishedBy',
                  },
                  {
                    title: '发布时间',
                    dataIndex: 'publishedAt',
                    render: (value) => formatShanghaiTime(value),
                  },
                ]}
              />
            </Card>
          </>
        ) : (
          !loadError && <Empty description="机构投递规则不存在" />
        )}
      </Space>
    );
  })();

  return (
    <PageContainer
      {...pageHeader(
        '投递与审核规则',
        '按机构发布不可变规则版本；已经开始的投递继续使用开始时冻结的旧版本。',
      )}
    >
      <DirectoryScopeBar scope={directoryScope} />
      {content}

      <Modal
        title="发布新的投递与审核规则"
        open={modalOpen}
        confirmLoading={submitting}
        okText="确认发布"
        cancelText="取消"
        onOk={() => void submit()}
        onCancel={() => !submitting && setModalOpen(false)}
        destroyOnClose
      >
        <Alert
          showIcon
          type="info"
          message="新规则只影响之后开始的投递"
          description="已有投递会话和订单继续使用开始时冻结的负余额下限与人工认定重量上限。"
          style={{ marginBottom: 16 }}
        />
        <Form<ReleaseForm>
          form={form}
          layout="vertical"
          disabled={!scopeReady || submitting}
        >
          <Form.Item
            name="reviewMode"
            label="审核方式"
            extra="当前阶段固定全部人工审核；自动审核尚未开放。"
          >
            <Select
              options={[
                {
                  label: '全部人工审核',
                  value: 'ALL_MANUAL',
                },
              ]}
              disabled
            />
          </Form.Item>
          <Form.Item
            name="openBalanceFloorYuan"
            label="负余额停投下限（元）"
            extra="用户可提现余额低于这个值时，不能开始下一次投递。"
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
            extra="用于防止审核人员误输入极端重量，允许范围为 0.001 至 1000.000 kg。"
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
    </PageContainer>
  );
}
