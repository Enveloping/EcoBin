import { useCallback, useEffect, useState } from 'react';
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import {
  Alert,
  App,
  Button,
  Card,
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
  listWithdrawals,
  reviewWithdrawal,
  type ReviewWithdrawalRequest,
  type WithdrawalOrder,
} from '@/api/funds';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { useAuthStore } from '@/stores/authStore';
import { formatMoneyCny, formatShanghaiTime } from '@/utils/decimal';
import { pageHeader } from '@/utils/pageStyle';

const STATUS: Record<string, { text: string; color: string }> = {
  PENDING_REVIEW: { text: '待人工审核', color: 'warning' },
  READY_TO_SUBMIT: { text: '审核通过，待提交', color: 'processing' },
  CHANNEL_PROCESSING: { text: '微信转账处理中', color: 'processing' },
  SUCCEEDED: { text: '提现成功', color: 'success' },
  REJECTED: { text: '审核拒绝', color: 'error' },
  LOCAL_CANCELLED: { text: '用户已取消', color: 'default' },
  LOCAL_ABORTED_BEFORE_CHANNEL: { text: '渠道前已终止', color: 'default' },
  CHANNEL_FAILED: { text: '微信转账失败', color: 'error' },
  CHANNEL_CANCELLED: { text: '微信转账已撤销', color: 'default' },
};

const CHANNEL_STATE: Record<string, { text: string; color: string }> = {
  ACCEPTED: { text: '微信已受理', color: 'processing' },
  PROCESSING: { text: '微信处理中', color: 'processing' },
  TRANSFERING: { text: '微信转账中', color: 'processing' },
  CANCELING: { text: '微信撤销处理中', color: 'processing' },
  WAIT_USER_CONFIRM: { text: '等待用户确认收款', color: 'warning' },
  SUCCESS: { text: '微信转账成功', color: 'success' },
  FAIL: { text: '微信转账失败', color: 'error' },
  CANCELLED: { text: '微信转账已撤销', color: 'default' },
};

const COLLECTION_MODE = {
  AUTHORIZED: { text: '授权后自动收款', color: 'green' },
  USER_CONFIRM: { text: '历史逐笔确认', color: 'default' },
} as const;

function errorText(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '提现订单加载失败';
}

export default function WithdrawalsPage() {
  const directory = useDirectoryScope();
  const organization = useOrganizationScope(directory);
  const session = useAuthStore((state) => state.session);
  const executeCommand = useCommandExecutor();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ note?: string }>();
  const [items, setItems] = useState<WithdrawalOrder[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [reviewing, setReviewing] = useState<{
    order: WithdrawalOrder;
    decision: 'APPROVED' | 'REJECTED';
  } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const mayReview = session?.accountType === 'PLATFORM_ADMIN'
    || !!session?.capabilities.includes('review.execute');

  const load = useCallback(async () => {
    if (!directory.context || !organization.organizationCode) return;
    setLoading(true);
    setError(undefined);
    try {
      const page = await listWithdrawals(
        directory.context,
        organization.organizationCode,
        { limit: 100 },
      );
      setItems(page.items);
    } catch (loadError) {
      setItems([]);
      setError(errorText(loadError));
    } finally {
      setLoading(false);
    }
  }, [directory.context, organization.organizationCode]);

  useEffect(() => {
    void load();
  }, [load]);

  const submitReview = async () => {
    if (
      !reviewing
      || !directory.context
      || !organization.organizationCode
    ) return;
    const values = await form.validateFields();
    const payload: ReviewWithdrawalRequest = {
      expectedVersion: reviewing.order.version,
      decision: reviewing.decision,
      note: values.note?.trim() || null,
    };
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'review-withdrawal',
          reviewing.order.withdrawalNo,
          payload,
        ),
        (intent) => reviewWithdrawal(
          directory.context!,
          organization.organizationCode!,
          reviewing.order.withdrawalNo,
          payload,
          intent,
        ),
      );
      message.success(
        reviewing.decision === 'APPROVED'
          ? reviewing.order.collectionMode === 'AUTHORIZED'
            ? '提现已审核通过，将按用户授权自动转入微信零钱'
            : '提现已审核通过，可靠任务将提交历史逐笔确认转账'
          : '提现已拒绝，双方冻结资金已释放',
      );
      setReviewing(null);
      form.resetFields();
      await load();
    } catch (reviewError) {
      message.error(errorText(reviewError));
      if (reviewError instanceof ApiProblem && reviewError.isVersionConflict) {
        await load();
      }
    } finally {
      setSubmitting(false);
    }
  };

  const content = (() => {
    if (directory.loading || organization.loading) {
      return <Card><Spin tip="正在确定提现审核范围" /></Card>;
    }
    if (!directory.context) return <Empty description="请选择目标租户" />;
    if (!organization.organizationOptions.length) {
      return <Empty description="当前租户尚无机构" />;
    }
    if (!organization.organizationCode) return <Spin />;
    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card>
          <Space wrap>
            <Typography.Text strong>提现归属机构</Typography.Text>
            <Select
              aria-label="提现归属机构"
              showSearch
              optionFilterProp="label"
              style={{ width: 380 }}
              value={organization.organizationCode}
              options={organization.organizationOptions}
              onChange={organization.setOrganizationCode}
            />
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新订单状态
            </Button>
          </Space>
        </Card>
        {error && <Alert type="error" showIcon message="提现订单加载失败" description={error} />}
        <Card title="提现订单">
          <Table<WithdrawalOrder>
            rowKey="withdrawalNo"
            loading={loading}
            dataSource={items}
            pagination={false}
            locale={{ emptyText: '当前机构暂无提现订单' }}
            columns={[
              { title: '提现单号', dataIndex: 'withdrawalNo', render: (value) => <Typography.Text copyable>{value}</Typography.Text> },
              { title: '金额', dataIndex: 'amountYuan', align: 'right', render: (value) => <Typography.Text strong>¥{formatMoneyCny(value)}</Typography.Text> },
              {
                title: '来源',
                dataIndex: 'sourceType',
                width: 190,
                render: (value, order) => (
                  <Space direction="vertical" size={2}>
                    <Tag color={value === 'DELIVERY_AUTO' ? 'cyan' : 'default'}>
                      {value === 'DELIVERY_AUTO' ? '投递自动提现' : '手动提现'}
                    </Tag>
                    {order.sourceDeliveryOrderNo && (
                      <Typography.Text type="secondary" copyable>
                        {order.sourceDeliveryOrderNo}
                      </Typography.Text>
                    )}
                  </Space>
                ),
              },
              {
                title: '收款方式',
                dataIndex: 'collectionMode',
                render: (value: WithdrawalOrder['collectionMode']) => {
                  const mode = COLLECTION_MODE[value];
                  return <Tag color={mode.color}>{mode.text}</Tag>;
                },
              },
              {
                title: '业务状态',
                dataIndex: 'status',
                render: (value) => {
                  const status = STATUS[value]
                    ?? { text: value, color: 'default' };
                  return <Tag color={status.color}>{status.text}</Tag>;
                },
              },
              {
                title: '微信处理结果',
                dataIndex: 'channelState',
                width: 360,
                render: (value, order) => {
                  if (!value && !order.channelErrorCode
                    && !order.channelStatusMessage) return '尚未提交';
                  const state = value
                    ? CHANNEL_STATE[value]
                      ?? { text: value, color: 'default' }
                    : null;
                  return (
                    <Space direction="vertical" size={2}>
                      <Space wrap size={4}>
                        {state
                          ? <Tag color={state.color}>{state.text}</Tag>
                          : (
                            <Typography.Text type="secondary">
                              尚未取得微信单据状态
                            </Typography.Text>
                          )}
                        {order.channelErrorCode && (
                          <Tag color="error">{order.channelErrorCode}</Tag>
                        )}
                      </Space>
                      {order.channelStatusMessage && (
                        <Typography.Text type="secondary">
                          {order.channelStatusMessage}
                        </Typography.Text>
                      )}
                    </Space>
                  );
                },
              },
              { title: '创建时间', dataIndex: 'createdAt', render: formatShanghaiTime },
              {
                title: '操作',
                fixed: 'right',
                render: (_, order) => order.status === 'PENDING_REVIEW' && mayReview
                  ? (
                    <Space>
                      <Button type="link" icon={<CheckOutlined />} onClick={() => setReviewing({ order, decision: 'APPROVED' })}>通过</Button>
                      <Button type="link" danger icon={<CloseOutlined />} onClick={() => setReviewing({ order, decision: 'REJECTED' })}>拒绝</Button>
                    </Space>
                  )
                  : '—',
              },
            ]}
          />
        </Card>
      </Space>
    );
  })();

  return (
    <PageContainer {...pageHeader('提现订单')}>
      <DirectoryScopeBar scope={directory} />
      {content}
      <Modal
        title={reviewing?.decision === 'APPROVED' ? '确认审核通过' : '确认拒绝提现'}
        open={!!reviewing}
        okText={reviewing?.decision === 'APPROVED' ? '通过并提交转账' : '拒绝并释放冻结'}
        okButtonProps={{ danger: reviewing?.decision === 'REJECTED' }}
        confirmLoading={submitting}
        onOk={() => void submitReview()}
        onCancel={() => setReviewing(null)}
      >
        <Alert
          style={{ marginBottom: 18 }}
          type={reviewing?.decision === 'APPROVED' ? 'warning' : 'info'}
          showIcon
          message={reviewing?.decision === 'APPROVED'
            ? '通过后将可靠提交微信转账'
            : '拒绝后会同步释放用户和机构冻结金额'}
          description={reviewing?.decision === 'APPROVED'
            ? reviewing.order.collectionMode === 'AUTHORIZED'
              ? '用户已在创建提现前完成自动收款授权。通过后系统会直接转账；微信返回未知、超时或余额不足时，不会擅自释放冻结或更换外部单号。'
              : '这是历史逐笔确认单。微信返回未知、超时或余额不足时，系统不会擅自释放冻结，也不会更换外部单号。'
            : '该决定不可通过修改订单回退，请确认审核事实。'}
        />
        {reviewing && (
          <DescriptionsSummary order={reviewing.order} />
        )}
        <Form form={form} layout="vertical" style={{ marginTop: 18 }}>
          <Form.Item name="note" label="审核备注">
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

function DescriptionsSummary({ order }: { order: WithdrawalOrder }) {
  return (
    <Card size="small">
      <Space direction="vertical" size={4}>
        <Typography.Text type="secondary">{order.withdrawalNo}</Typography.Text>
        <Typography.Title level={3} style={{ margin: 0 }}>
          ¥{formatMoneyCny(order.amountYuan)}
        </Typography.Title>
      </Space>
    </Card>
  );
}
