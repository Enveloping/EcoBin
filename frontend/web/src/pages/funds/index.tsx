import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckCircleOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  QRCode,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  createRechargeOrder,
  disableMerchantBinding,
  getMerchantBinding,
  getPayoutAccount,
  getWithdrawalConfiguration,
  listRechargeOrders,
  verifyMerchantBinding,
  type MerchantBinding,
  type PayoutAccount,
  type RechargeOrder,
  type WithdrawalConfiguration,
} from '@/api/funds';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { useAuthStore } from '@/stores/authStore';
import { formatMoneyCny, formatShanghaiTime } from '@/utils/decimal';
import { pageHeader } from '@/utils/pageStyle';

const MONEY = /^(0|[1-9][0-9]*)\.[0-9]{2}$/;

const RECHARGE_STATUS: Record<string, { text: string; color: string }> = {
  PENDING_PAYMENT: { text: '等待支付', color: 'processing' },
  PAID_PENDING_POST: { text: '已支付，待入账', color: 'warning' },
  POSTED: { text: '已入账', color: 'success' },
  CLOSED: { text: '已关闭', color: 'default' },
  EXPIRED: { text: '已过期', color: 'default' },
};

function errorText(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '机构资金加载失败';
}

export default function FundsPage() {
  const directory = useDirectoryScope();
  const organization = useOrganizationScope(directory);
  const session = useAuthStore((state) => state.session);
  const executeCommand = useCommandExecutor();
  const { message } = App.useApp();
  const [rechargeForm] = Form.useForm<{ grossAmountYuan: string }>();
  const [account, setAccount] = useState<PayoutAccount | null>(null);
  const [recharges, setRecharges] = useState<RechargeOrder[]>([]);
  const [configuration, setConfiguration] =
    useState<WithdrawalConfiguration | null>(null);
  const [binding, setBinding] = useState<MerchantBinding | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [rechargeOpen, setRechargeOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [createdRecharge, setCreatedRecharge] =
    useState<RechargeOrder | null>(null);

  const mayCreateRecharge = directory.context?.domain !== 'platform'
    && !!session?.capabilities.includes('recharge.create');

  const load = useCallback(async () => {
    if (!directory.context || !organization.organizationCode) return;
    setLoading(true);
    setError(undefined);
    try {
      const [nextAccount, nextRecharges, nextConfiguration, nextBinding] =
        await Promise.all([
          getPayoutAccount(directory.context, organization.organizationCode),
          listRechargeOrders(
            directory.context,
            organization.organizationCode,
            { limit: 50 },
          ),
          getWithdrawalConfiguration(
            directory.context,
            organization.organizationCode,
          ),
          directory.platform
            ? getMerchantBinding(
              directory.context,
              organization.organizationCode,
            )
            : Promise.resolve(null),
        ]);
      setAccount(nextAccount);
      setRecharges(nextRecharges.items);
      setConfiguration(nextConfiguration);
      setBinding(nextBinding);
    } catch (loadError) {
      setError(errorText(loadError));
      setAccount(null);
      setRecharges([]);
      setConfiguration(null);
      setBinding(null);
    } finally {
      setLoading(false);
    }
  }, [directory.context, directory.platform, organization.organizationCode]);

  useEffect(() => {
    void load();
  }, [load]);

  const submitRecharge = async () => {
    if (!directory.context || !organization.organizationCode) return;
    const values = await rechargeForm.validateFields();
    const payload = { grossAmountYuan: values.grossAmountYuan.trim() };
    setSubmitting(true);
    try {
      const created = await executeCommand(
        commandKey(
          'create-recharge',
          organization.organizationCode,
          payload,
        ),
        (intent) => createRechargeOrder(
          directory.context!,
          organization.organizationCode!,
          payload,
          intent,
        ),
      );
      setCreatedRecharge(created);
      setRechargeOpen(false);
      message.success('充值单已创建，正在准备微信支付二维码');
      await load();
    } catch (submitError) {
      message.error(errorText(submitError));
    } finally {
      setSubmitting(false);
    }
  };

  const updateBinding = async (enable: boolean) => {
    if (
      !directory.context
      || !organization.organizationCode
      || !binding
    ) return;
    const operation = enable
      ? 'verify-merchant-binding'
      : 'disable-merchant-binding';
    const target = organization.organizationCode;
    setSubmitting(true);
    try {
      const updated = enable
        ? await (() => {
          const payload = {
        expectedMiniappVersion: binding.miniappVersion,
        expectedBindingVersion: binding.bindingVersion ?? null,
        note: '平台管理员已核对微信商户平台 AppID 关联关系',
          };
          return executeCommand(
            commandKey(operation, target, payload),
            (intent) => verifyMerchantBinding(
            directory.context!,
            organization.organizationCode!,
            payload,
            intent,
            ),
          );
        })()
        : await (() => {
          const payload = {
            expectedBindingVersion: binding.bindingVersion!,
            reason: '平台管理员手动禁用本地渠道就绪事实',
          };
          return executeCommand(
            commandKey(operation, target, payload),
            (intent) => disableMerchantBinding(
            directory.context!,
            organization.organizationCode!,
            payload,
            intent,
            ),
          );
        })();
      setBinding(updated);
      message.success(enable ? '商户绑定已标记为已验证' : '商户绑定已禁用');
      await load();
    } catch (updateError) {
      message.error(errorText(updateError));
    } finally {
      setSubmitting(false);
    }
  };

  const readiness = useMemo(() => {
    if (!account) return { ready: false, text: '状态未知' };
    const ready = account.merchantBindingStatus === 'VERIFIED'
      && account.payoutGateStatus === 'OPEN';
    return { ready, text: ready ? '微信资金通道就绪' : '微信资金通道未就绪' };
  }, [account]);

  const content = (() => {
    if (directory.loading || organization.loading) {
      return <Card><Spin tip="正在确定资金作用范围" /></Card>;
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
            <Typography.Text strong>资金归属机构</Typography.Text>
            <Select
              aria-label="资金归属机构"
              showSearch
              optionFilterProp="label"
              style={{ width: 380 }}
              value={organization.organizationCode}
              options={organization.organizationOptions}
              onChange={organization.setOrganizationCode}
            />
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新资金事实
            </Button>
          </Space>
        </Card>

        {error && <Alert type="error" showIcon message="资金数据加载失败" description={error} />}

        {loading ? <Card><Spin tip="正在读取机构钱包和渠道状态" /></Card> : account && (
          <>
            <Card
              styles={{ body: { padding: 0 } }}
              style={{ overflow: 'hidden' }}
            >
              <div style={{ padding: 28, background: '#0B4F4B', color: '#fff' }}>
                <Row gutter={[24, 24]} align="middle">
                  <Col flex="auto">
                    <Typography.Text style={{ color: 'rgba(255,255,255,.72)', letterSpacing: 2 }}>
                      ORGANIZATION PAYOUT ACCOUNT
                    </Typography.Text>
                    <Statistic
                      title={<span style={{ color: 'rgba(255,255,255,.78)' }}>机构可出款余额</span>}
                      value={formatMoneyCny(account.availablePayoutYuan)}
                      prefix="¥"
                      valueStyle={{ color: '#fff', fontSize: 38, fontWeight: 720 }}
                    />
                  </Col>
                  <Col>
                    <Space direction="vertical" align="end">
                      <Tag icon={readiness.ready ? <CheckCircleOutlined /> : <SafetyCertificateOutlined />} color={readiness.ready ? 'success' : 'warning'}>
                        {readiness.text}
                      </Tag>
                      {mayCreateRecharge && (
                        <Button type="primary" ghost icon={<PlusOutlined />} onClick={() => setRechargeOpen(true)}>
                          创建机构充值
                        </Button>
                      )}
                    </Space>
                  </Col>
                </Row>
              </div>
              <Row gutter={0}>
                {[
                  ['提现冻结', account.frozenWithdrawalYuan],
                  ['累计充值净额', account.cumulativeRechargeNetYuan],
                  ['累计成功提现', account.cumulativeSuccessfulWithdrawalYuan],
                  ['累计支付手续费', account.cumulativeRechargeFeeYuan],
                ].map(([label, value]) => (
                  <Col xs={12} lg={6} key={label} style={{ padding: '20px 24px', borderRight: '1px solid #E2E8F0' }}>
                    <Statistic title={label} value={formatMoneyCny(value)} prefix="¥" valueStyle={{ fontSize: 20 }} />
                  </Col>
                ))}
              </Row>
            </Card>

            <Row gutter={[16, 16]}>
              <Col xs={24} lg={directory.platform ? 12 : 24}>
                <Card title={<Space><WalletOutlined />提现规则</Space>}>
                  {configuration && (
                    <Descriptions column={2} size="small">
                      <Descriptions.Item label="当前版本">v{configuration.versionNo}</Descriptions.Item>
                      <Descriptions.Item label="人工审核">全部提现</Descriptions.Item>
                      <Descriptions.Item label="单笔范围">¥{configuration.manualMinimumYuan} — ¥{configuration.manualMaximumYuan}</Descriptions.Item>
                      <Descriptions.Item label="硬上限">¥{configuration.hardLimitYuan}</Descriptions.Item>
                    </Descriptions>
                  )}
                </Card>
              </Col>
              {directory.platform && binding && (
                <Col xs={24} lg={12}>
                  <Card
                    title={<Space><LinkOutlined />微信商户绑定核查</Space>}
                    extra={binding.status === 'VERIFIED'
                      ? <Button danger loading={submitting} onClick={() => void updateBinding(false)}>禁用</Button>
                      : <Button type="primary" loading={submitting} onClick={() => void updateBinding(true)}>标记已核查</Button>}
                  >
                    <Descriptions column={2} size="small">
                      <Descriptions.Item label="AppID">{binding.appId}</Descriptions.Item>
                      <Descriptions.Item label="小程序版本">v{binding.miniappVersion}</Descriptions.Item>
                      <Descriptions.Item label="绑定状态"><Tag color={binding.status === 'VERIFIED' ? 'success' : 'warning'}>{binding.status}</Tag></Descriptions.Item>
                      <Descriptions.Item label="系统商户号">{binding.merchantId ?? '核查后显示'}</Descriptions.Item>
                    </Descriptions>
                  </Card>
                </Col>
              )}
            </Row>

            <Card title="机构充值记录" extra={<Typography.Text type="secondary">手续费按 0.6% 向上取整到分</Typography.Text>}>
              <Table<RechargeOrder>
                rowKey="rechargeNo"
                pagination={false}
                dataSource={recharges}
                locale={{ emptyText: '尚未创建机构充值单' }}
                columns={[
                  { title: '充值单号', dataIndex: 'rechargeNo', render: (value) => <Typography.Text copyable>{value}</Typography.Text> },
                  { title: '充值金额', dataIndex: 'grossAmountYuan', align: 'right', render: (value) => `¥${formatMoneyCny(value)}` },
                  { title: '手续费', dataIndex: 'feeYuan', align: 'right', render: (value) => `¥${formatMoneyCny(value)}` },
                  { title: '机构净入账', dataIndex: 'netAmountYuan', align: 'right', render: (value) => <Typography.Text strong>¥{formatMoneyCny(value)}</Typography.Text> },
                  { title: '状态', dataIndex: 'status', render: (value) => { const status = RECHARGE_STATUS[value] ?? { text: value, color: 'default' }; return <Tag color={status.color}>{status.text}</Tag>; } },
                  { title: '创建时间', dataIndex: 'createdAt', render: formatShanghaiTime },
                  { title: '支付二维码', render: (_, row) => row.qrCodeUrl && row.status === 'PENDING_PAYMENT' ? <Button type="link" onClick={() => setCreatedRecharge(row)}>查看</Button> : '—' },
                ]}
              />
            </Card>
          </>
        )}
      </Space>
    );
  })();

  return (
    <PageContainer {...pageHeader('机构资金', '机构钱包、充值、提现额度与微信渠道就绪事实')}>
      <DirectoryScopeBar scope={directory} />
      {content}

      <Modal title="创建机构充值" open={rechargeOpen} confirmLoading={submitting} onOk={() => void submitRecharge()} onCancel={() => setRechargeOpen(false)} okText="创建并准备二维码">
        <Alert style={{ marginBottom: 18 }} type="info" showIcon message="微信实际扣款前不会增加机构余额" description="用户扫码支付成功后，系统再以可信回调或主动查单结果入账。" />
        <Form form={rechargeForm} layout="vertical">
          <Form.Item name="grossAmountYuan" label="充值金额（元）" rules={[{ required: true, message: '请输入充值金额' }, { pattern: MONEY, message: '请输入精确到分的金额，例如 100.00' }]}>
            <Input prefix="¥" placeholder="1000.00" inputMode="decimal" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="微信 Native 支付二维码" open={!!createdRecharge} footer={<Button onClick={() => setCreatedRecharge(null)}>关闭</Button>} onCancel={() => setCreatedRecharge(null)}>
        {createdRecharge?.qrCodeUrl ? (
          <Space direction="vertical" align="center" style={{ width: '100%', padding: 24 }}>
            <QRCode value={createdRecharge.qrCodeUrl} size={236} />
            <Typography.Title level={4}>¥{formatMoneyCny(createdRecharge.grossAmountYuan)}</Typography.Title>
            <Typography.Text type="secondary">支付结果以后端订单状态为准，请勿根据扫码页面直接入账。</Typography.Text>
          </Space>
        ) : (
          <Alert type="warning" showIcon message="二维码仍在准备" description="可靠任务正在向微信创建原单，请稍后刷新充值记录。" />
        )}
      </Modal>
    </PageContainer>
  );
}
