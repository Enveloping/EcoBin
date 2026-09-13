import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CheckCircleOutlined,
  CreditCardOutlined,
  QrcodeOutlined,
} from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Divider,
  Input,
  QRCode,
  Radio,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import {
  createRechargeOrder,
  getRechargeOrder,
  listRechargeOrders,
  type RechargeOrder,
} from '@/api/funds';
import type { DirectoryContext } from '@/api/identityDirectory';
import HelpTip from '@/components/HelpTip';
import { rechargePreview } from './rechargePreview';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import {
  compareMoneyCny,
  formatMoneyCny,
  formatShanghaiTime,
  isMoneyInputDraft,
  normalizeMoneyInput,
} from '@/utils/decimal';

const PRESET_AMOUNTS = ['1.00', '50.00', '200.00', '1000.00', '5000.00'] as const;
const CUSTOM_AMOUNT = 'CUSTOM';
const MAX_RECHARGE_YUAN = '200000.00';
const TERMINAL_STATUSES = new Set(['POSTED', 'CLOSED', 'EXPIRED']);

type AmountChoice = typeof PRESET_AMOUNTS[number] | typeof CUSTOM_AMOUNT;

interface TrackedRecharge {
  order: RechargeOrder;
  ownerId: number;
  scopeKey: string;
  amountSnapshot: string;
}

interface OrganizationRechargeCardProps {
  context: DirectoryContext;
  organizationCode: string;
  onPosted: () => void | Promise<void>;
}

function errorText(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '充值二维码生成失败';
}

function newestOrder(orders: RechargeOrder[]): RechargeOrder | null {
  return [...orders].sort((left, right) =>
    Date.parse(right.createdAt) - Date.parse(left.createdAt))[0] ?? null;
}

function amountChoice(amountYuan: string): {
  choice: AmountChoice;
  customAmount: string;
} {
  const preset = PRESET_AMOUNTS.find((amount) => amount === amountYuan);
  return preset
    ? { choice: preset, customAmount: '' }
    : { choice: CUSTOM_AMOUNT, customAmount: amountYuan };
}

function isRechargeAmountValid(amountYuan: string | null): amountYuan is string {
  return !!amountYuan
    && compareMoneyCny(amountYuan, '1.00') >= 0
    && compareMoneyCny(amountYuan, MAX_RECHARGE_YUAN) <= 0;
}

export default function OrganizationRechargeCard({
  context,
  organizationCode,
  onPosted,
}: OrganizationRechargeCardProps) {
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const ownerSequence = useRef(0);
  const intentGeneration = useRef(0);
  const pollAbort = useRef<AbortController>();
  const [choice, setChoice] = useState<AmountChoice>();
  const [customAmount, setCustomAmount] = useState('');
  const [tracked, setTracked] = useState<TrackedRecharge | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [paymentLocked, setPaymentLocked] = useState(false);
  const [issue, setIssue] = useState<string>();
  const [previousCodeMayBeActive, setPreviousCodeMayBeActive] = useState(false);
  const scopeKey = JSON.stringify([
    context.domain,
    context.tenantCode ?? null,
    organizationCode,
  ]);

  const normalizedAmount = useMemo(() => {
    const draft = choice === CUSTOM_AMOUNT ? customAmount : choice ?? '';
    const normalized = normalizeMoneyInput(draft);
    return isRechargeAmountValid(normalized) ? normalized : null;
  }, [choice, customAmount]);

  const invalidateVisibleOrder = () => {
    if (tracked?.order.status === 'PENDING_PAYMENT' || preparing) {
      setPreviousCodeMayBeActive(true);
    }
    ownerSequence.current += 1;
    intentGeneration.current += 1;
    pollAbort.current?.abort();
    pollAbort.current = undefined;
    setTracked(null);
    setPreparing(false);
    setPaymentLocked(false);
    setIssue(undefined);
  };

  const selectAmount = (nextChoice: AmountChoice) => {
    if (nextChoice === choice) return;
    invalidateVisibleOrder();
    setChoice(nextChoice);
  };

  const changeCustomAmount = (next: string) => {
    if (!isMoneyInputDraft(next) || next === customAmount) return;
    invalidateVisibleOrder();
    setChoice(CUSTOM_AMOUNT);
    setCustomAmount(next);
  };

  useEffect(() => {
    const ownerId = ++ownerSequence.current;
    setPreviousCodeMayBeActive(false);
    const controller = new AbortController();
    pollAbort.current?.abort();
    pollAbort.current = controller;
    setTracked(null);
    setPreparing(false);
    setPaymentLocked(false);
    setIssue(undefined);

    void Promise.all([
      listRechargeOrders(
        context,
        organizationCode,
        { status: 'PENDING_PAYMENT', limit: 1 },
        controller.signal,
      ),
      listRechargeOrders(
        context,
        organizationCode,
        { status: 'PAID_PENDING_POST', limit: 1 },
        controller.signal,
      ),
    ]).then(([pending, posting]) => {
      if (
        controller.signal.aborted
        || ownerSequence.current !== ownerId
      ) return;
      const recovered = newestOrder([...pending.items, ...posting.items]);
      if (!recovered) return;
      const recoveredAmount = amountChoice(recovered.grossAmountYuan);
      setChoice(recoveredAmount.choice);
      setCustomAmount(recoveredAmount.customAmount);
      setTracked({
        order: recovered,
        ownerId,
        scopeKey,
        amountSnapshot: recovered.grossAmountYuan,
      });
      setPaymentLocked(
        recovered.status === 'PENDING_PAYMENT' && !recovered.qrCodeUrl,
      );
    }).catch((error) => {
      if (controller.signal.aborted || ownerSequence.current !== ownerId) return;
      setIssue(errorText(error));
    });

    return () => {
      controller.abort();
      if (pollAbort.current === controller) pollAbort.current = undefined;
    };
  }, [context, organizationCode, scopeKey]);

  useEffect(() => {
    const current = tracked;
    if (!current || current.scopeKey !== scopeKey) return undefined;
    if (TERMINAL_STATUSES.has(current.order.status)) return undefined;

    const controller = new AbortController();
    pollAbort.current?.abort();
    pollAbort.current = controller;
    const waitMs = current.order.qrCodeUrl
      ? Math.max(3000, current.order.recommendedPollAfterMs)
      : Math.max(1000, current.order.recommendedPollAfterMs);
    const timer = window.setTimeout(() => {
      void getRechargeOrder(
        context,
        organizationCode,
        current.order.rechargeNo,
        controller.signal,
      ).then((next) => {
        if (
          controller.signal.aborted
          || ownerSequence.current !== current.ownerId
          || current.scopeKey !== scopeKey
        ) return;
        const becameReady = !current.order.qrCodeUrl && !!next.qrCodeUrl;
        setTracked({ ...current, order: next });
        setIssue(undefined);
        setPreparing(false);
        if (
          becameReady
          || next.status !== 'PENDING_PAYMENT'
          || TERMINAL_STATUSES.has(next.status)
        ) {
          intentGeneration.current += 1;
          setPaymentLocked(false);
        }
        if (next.status === 'POSTED') void onPosted();
      }).catch((error) => {
        if (
          controller.signal.aborted
          || ownerSequence.current !== current.ownerId
        ) return;
        setIssue(errorText(error));
        setPreparing(false);
        setPaymentLocked(false);
      });
    }, waitMs);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (pollAbort.current === controller) pollAbort.current = undefined;
    };
  }, [context, onPosted, organizationCode, scopeKey, tracked]);

  const submit = async () => {
    if (!normalizedAmount) return;
    const amountSnapshot = normalizedAmount;
    const ownerId = ++ownerSequence.current;
    const attemptGeneration = intentGeneration.current;
    pollAbort.current?.abort();
    pollAbort.current = undefined;
    setTracked(null);
    setPreparing(true);
    setPaymentLocked(true);
    setIssue(undefined);
    const payload = { grossAmountYuan: amountSnapshot };
    try {
      const created = await executeCommand(
        commandKey(
          'create-recharge',
          `${organizationCode}:${attemptGeneration}`,
          payload,
        ),
        (intent) => createRechargeOrder(
          context,
          organizationCode,
          payload,
          intent,
        ),
      );
      if (ownerSequence.current !== ownerId) return;
      setTracked({
        order: created,
        ownerId,
        scopeKey,
        amountSnapshot,
      });
      setPreparing(false);
      const readyToCreateAgain = !!created.qrCodeUrl
        || created.status !== 'PENDING_PAYMENT';
      if (readyToCreateAgain) {
        intentGeneration.current += 1;
        setPaymentLocked(false);
      }
      message.success(
        created.qrCodeUrl
          ? '支付二维码已生成'
          : '充值单已创建，正在生成支付二维码',
      );
      if (created.status === 'POSTED') void onPosted();
    } catch (error) {
      if (ownerSequence.current !== ownerId) return;
      setPreparing(false);
      setPaymentLocked(false);
      setIssue(errorText(error));
      message.error(errorText(error));
      if (!(error instanceof ApiProblem) || !error.retryable) {
        intentGeneration.current += 1;
      }
    }
  };

  const order = tracked?.order;
  const quote = normalizedAmount ? rechargePreview(normalizedAmount) : null;
  const amounts = order?.grossAmountYuan === normalizedAmount ? order : quote;
  const amountHelp = choice === CUSTOM_AMOUNT && customAmount
    && !normalizedAmount
    ? `请输入 1.00 至 ${MAX_RECHARGE_YUAN} 元，最多两位小数`
    : undefined;

  return (
    <Card
      title={(
        <Space>
          <QrcodeOutlined />
          <span>机构充值</span>
        </Space>
      )}
      extra={(
        <HelpTip label="充值手续费">手续费为充值金额的 0.6%，不足一分按一分计算。</HelpTip>
      )}
    >
      {previousCodeMayBeActive && <Alert type="warning" showIcon message="之前的充值二维码可能仍有效，请勿重复付款。" style={{ marginBottom: 16 }} />}
      <Radio.Group
        aria-label="充值金额"
        value={choice}
        onChange={(event) => selectAmount(event.target.value as AmountChoice)}
        style={{ width: '100%' }}
      >
        <Row gutter={[10, 10]}>
          {PRESET_AMOUNTS.map((amount) => (
            <Col xs={12} md={8} xl={4} key={amount}>
              <Radio.Button
                value={amount}
                style={{ width: '100%', height: 54, lineHeight: '52px', textAlign: 'center' }}
              >
                <Typography.Text strong>¥{formatMoneyCny(amount)}</Typography.Text>
              </Radio.Button>
            </Col>
          ))}
          <Col xs={12} md={8} xl={4}>
            <Radio.Button
              value={CUSTOM_AMOUNT}
              style={{ width: '100%', height: 54, lineHeight: '52px', textAlign: 'center' }}
            >
              自定义充值
            </Radio.Button>
          </Col>
        </Row>
      </Radio.Group>

      {choice === CUSTOM_AMOUNT && (
        <div style={{ maxWidth: 420, marginTop: 14 }}>
          <Input
            aria-label="自定义充值金额"
            prefix="¥"
            inputMode="decimal"
            placeholder="例如 1000 或 1000.5"
            status={amountHelp ? 'error' : undefined}
            value={customAmount}
            onChange={(event) => changeCustomAmount(event.target.value)}
            onBlur={() => {
              const normalized = normalizeMoneyInput(customAmount);
              if (normalized) setCustomAmount(normalized);
            }}
          />
          {amountHelp && (
            <Typography.Text type="danger" style={{ fontSize: 12 }}>
              {amountHelp}
            </Typography.Text>
          )}
        </div>
      )}

      <Space wrap style={{ marginTop: 18 }}>
        <Button
          type="primary"
          size="large"
          icon={<CreditCardOutlined />}
          disabled={!normalizedAmount || paymentLocked}
          loading={preparing && paymentLocked}
          onClick={() => void submit()}
        >
          支付
        </Button>
        {normalizedAmount && (
          <Typography.Text type="secondary">
            本次充值金额 ¥{formatMoneyCny(normalizedAmount)}
            {amounts && <> · 手续费 ¥{formatMoneyCny(amounts.feeYuan)} · 预计到账 ¥{formatMoneyCny(amounts.netAmountYuan)}</>}
          </Typography.Text>
        )}
      </Space>

      {(preparing || order || issue) && <Divider />}
      {issue && (
        <Alert
          showIcon
          type="error"
          message="充值状态读取失败"
          description={issue}
          style={{ marginBottom: order || preparing ? 16 : 0 }}
        />
      )}
      {preparing && !order && (
        <div style={{ padding: '28px 0', textAlign: 'center' }}>
          <Spin tip="正在创建充值单并生成微信支付二维码" />
        </div>
      )}
      {order?.status === 'PENDING_PAYMENT' && order.qrCodeUrl && (
        <Row gutter={[28, 20]} align="middle">
          <Col flex="none">
            <QRCode value={order.qrCodeUrl} size={220} />
          </Col>
          <Col flex="auto">
            <Space direction="vertical" size={8}>
              <Tag color="processing">等待微信支付</Tag>
              <Typography.Title level={3} style={{ margin: 0 }}>
                ¥{formatMoneyCny(order.grossAmountYuan)}
              </Typography.Title>
              <Typography.Text>
                请使用微信扫描二维码完成支付。
              </Typography.Text>
              <Typography.Text type="secondary">
                二维码有效期至 {order.expiresAt
                  ? formatShanghaiTime(order.expiresAt)
                  : '以微信支付页面为准'}
              </Typography.Text>
              <Typography.Text type="warning">
                再次点击支付会创建新充值单，不会使当前二维码失效。
              </Typography.Text>
            </Space>
          </Col>
        </Row>
      )}
      {order?.status === 'PENDING_PAYMENT' && !order.qrCodeUrl && !preparing && (
        <Alert
          showIcon
          type={order.paymentPreparationStatus === 'RETRYING' ? 'warning' : 'info'}
          message={order.paymentPreparationStatus === 'RETRYING'
            ? '微信支付二维码正在重试生成'
            : '微信支付二维码正在准备'}
          description="系统会继续查询二维码状态；切换金额后可以立即发起新的充值。"
        />
      )}
      {order?.status === 'PAID_PENDING_POST' && (
        <Alert
          showIcon
          type="info"
          message="微信支付已确认，机构余额正在入账"
          description={`充值单 ${order.rechargeNo}`}
        />
      )}
      {order?.status === 'POSTED' && (
        <Alert
          showIcon
          type="success"
          icon={<CheckCircleOutlined />}
          message={`充值已入账 ¥${formatMoneyCny(order.netAmountYuan)}`}
          description={`充值金额 ¥${formatMoneyCny(order.grossAmountYuan)}，手续费 ¥${formatMoneyCny(order.feeYuan)}。`}
        />
      )}
      {(order?.status === 'CLOSED' || order?.status === 'EXPIRED') && (
        <Alert
          showIcon
          type="warning"
          message={order.status === 'EXPIRED' ? '充值单已过期' : '充值单已关闭'}
          description="该充值单没有增加机构余额，可以重新选择金额并支付。"
        />
      )}
    </Card>
  );
}
