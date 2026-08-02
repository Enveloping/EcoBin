import { useEffect, useRef, useState } from 'react';
import {
  ModalForm,
  ProFormRadio,
  ProFormText,
  ProFormTextArea,
} from '@ant-design/pro-components';
import { Alert, Descriptions, Form, Tag, Typography } from 'antd';
import type {
  DeliveryOrderDetail,
  DeliveryReviewDecision,
  DeliveryReviewPreview,
  DeliveryReviewPreviewRequest,
  DeliveryReviewRequest,
} from '@/api/deliveryOrders';
import { ApiProblem } from '@/api/request';
import {
  formatBusinessWeight,
  formatMoneyCny,
  formatUnitPrice,
} from '@/utils/decimal';
import {
  calculateAmountForWeight,
  calculateClientReviewPreview,
  convertAmountToWeight,
  isCanonicalReviewAmount,
  isCanonicalReviewWeight,
  isWeightWithinAbsoluteLimit,
  reviewPreviewMatches,
  type AmountToWeightConversion,
  type ClientDeliveryReviewPreview,
} from './deliveryReviewMath';

export type DeliveryMutationKind = 'review' | 'correction';

interface DeliveryReviewFormValues {
  decision: DeliveryReviewDecision;
  finalWeightKg?: string;
  targetAmountYuan?: string;
  reason?: string;
}

interface DeliveryReviewModalProps {
  kind: DeliveryMutationKind;
  order: DeliveryOrderDetail | null;
  open: boolean;
  submitting: boolean;
  onOpenChange: (open: boolean) => void;
  onPreview: (
    request: DeliveryReviewPreviewRequest,
    signal: AbortSignal,
  ) => Promise<DeliveryReviewPreview>;
  onPreviewVersionConflict: () => void | Promise<void>;
  onSubmit: (request: DeliveryReviewRequest) => Promise<boolean>;
}

interface ConversionView extends AmountToWeightConversion {
  source: 'weight' | 'amount';
}

type PreviewState =
  | { phase: 'idle'; signature: null }
  | { phase: 'waiting' | 'loading'; signature: string }
  | {
      phase: 'verified';
      signature: string;
      server: DeliveryReviewPreview;
    }
  | {
      phase: 'mismatch';
      signature: string;
      server: DeliveryReviewPreview;
    }
  | { phase: 'error'; signature: string; message: string };

const TWO_DECIMAL_WEIGHT = /^-?(0|[1-9]\d*)\.\d{2}$/;
const TWO_DECIMAL_MONEY = /^-?(0|[1-9]\d*)\.\d{2}$/;

function valueOrDash(value: string | null): string {
  return value === null ? '—' : value;
}

function previewSignature(
  request: DeliveryReviewPreviewRequest,
  targetAmountYuan: string | undefined,
): string {
  return [
    request.expectedRevisionNo.toString(),
    request.decision,
    request.finalWeightKg ?? '',
    targetAmountYuan ?? '',
  ].join('|');
}

function previewRequest(
  order: DeliveryOrderDetail,
  decision: DeliveryReviewDecision | undefined,
  finalWeightKg: string | undefined,
): DeliveryReviewPreviewRequest | null {
  if (decision === 'ORIGINAL_APPROVED') {
    return {
      expectedRevisionNo: order.review.currentRevisionNo,
      decision,
      finalWeightKg: null,
    };
  }
  if (decision !== 'MODIFIED_APPROVED'
      || !finalWeightKg
      || !isCanonicalReviewWeight(finalWeightKg)
      || !isWeightWithinAbsoluteLimit(
        finalWeightKg,
        order.review.maxReviewAbsoluteWeightKg,
      )) {
    return null;
  }
  return {
    expectedRevisionNo: order.review.currentRevisionNo,
    decision,
    finalWeightKg,
  };
}

function clientPreview(
  order: DeliveryOrderDetail,
  request: DeliveryReviewPreviewRequest,
): ClientDeliveryReviewPreview {
  return calculateClientReviewPreview(
    order,
    request.decision,
    request.finalWeightKg,
  );
}

function conversionFromWeight(
  order: DeliveryOrderDetail,
  weightKg: string,
): ConversionView {
  const amountYuan = calculateAmountForWeight(
    weightKg,
    order.raw.unitPriceYuanPerKg,
  );
  if (order.raw.unitPriceYuanPerKg === null) {
    return {
      source: 'weight',
      targetAmountYuan: amountYuan,
      candidateWeightKg: weightKg,
      actualAmountYuan: amountYuan,
      exact: true,
      realizingWeightMinKg: null,
      realizingWeightMaxKg: null,
      realizingCandidateCount: '0',
    };
  }
  const reverse = convertAmountToWeight(
    amountYuan,
    order.raw.unitPriceYuanPerKg,
    order.review.maxReviewAbsoluteWeightKg,
  );
  return {
    ...reverse,
    source: 'weight',
    candidateWeightKg: weightKg,
    actualAmountYuan: amountYuan,
    exact: true,
  };
}

function initialModifiedWeight(
  kind: DeliveryMutationKind,
  order: DeliveryOrderDetail,
): string | undefined {
  if (kind === 'correction') {
    return order.review.finalWeightKg ?? order.raw.weightKg ?? undefined;
  }
  return order.raw.weightKg ?? undefined;
}

function DeliveryReviewModalForm({
  kind,
  order,
  open,
  submitting,
  onOpenChange,
  onPreview,
  onPreviewVersionConflict,
  onSubmit,
}: DeliveryReviewModalProps & { order: DeliveryOrderDetail }) {
  const rawCanBeApproved =
    order.raw.weightReliability === 'RELIABLE'
    && order.raw.amountReliability === 'RELIABLE'
    && order.raw.weightKg !== null
    && order.raw.amountYuan !== null;
  const initialDecision: DeliveryReviewDecision =
    kind === 'correction' || !rawCanBeApproved
      ? 'MODIFIED_APPROVED'
      : 'ORIGINAL_APPROVED';
  const seedWeight = initialDecision === 'MODIFIED_APPROVED'
    ? initialModifiedWeight(kind, order)
    : undefined;
  const seedConversion = seedWeight && isCanonicalReviewWeight(seedWeight)
    ? conversionFromWeight(order, seedWeight)
    : null;

  const [form] = Form.useForm<DeliveryReviewFormValues>();
  const decision = Form.useWatch('decision', form) ?? initialDecision;
  const watchedWeight = Form.useWatch('finalWeightKg', form);
  const watchedAmount = Form.useWatch('targetAmountYuan', form);
  const [conversion, setConversion] =
    useState<ConversionView | null>(seedConversion);
  const [previewState, setPreviewState] = useState<PreviewState>({
    phase: 'idle',
    signature: null,
  });
  const previewSequence = useRef(0);
  const activePreview = useRef<AbortController | null>(null);

  const invalidatePreview = () => {
    previewSequence.current += 1;
    activePreview.current?.abort();
    activePreview.current = null;
    setPreviewState({ phase: 'idle', signature: null });
  };

  useEffect(() => {
    invalidatePreview();
    if (!open) return undefined;

    const request = previewRequest(
      order,
      decision,
      watchedWeight?.trim(),
    );
    if (!request) return undefined;
    if (decision === 'MODIFIED_APPROVED'
        && (!watchedAmount
          || !isCanonicalReviewAmount(watchedAmount.trim()))) {
      return undefined;
    }

    let expected: ClientDeliveryReviewPreview;
    try {
      expected = clientPreview(order, request);
    } catch {
      return undefined;
    }
    const signature = previewSignature(
      request,
      watchedAmount?.trim(),
    );
    const sequence = previewSequence.current;
    setPreviewState({ phase: 'waiting', signature });
    const timer = window.setTimeout(async () => {
      if (previewSequence.current !== sequence) return;
      const controller = new AbortController();
      activePreview.current = controller;
      setPreviewState({ phase: 'loading', signature });
      try {
        const server = await onPreview(request, controller.signal);
        if (controller.signal.aborted
            || previewSequence.current !== sequence) {
          return;
        }
        setPreviewState(
          reviewPreviewMatches(expected, server)
            ? { phase: 'verified', signature, server }
            : { phase: 'mismatch', signature, server },
        );
      } catch (error) {
        if (controller.signal.aborted
            || previewSequence.current !== sequence) {
          return;
        }
        if (error instanceof ApiProblem
            && error.code === 'DELIVERY.REVISION_VERSION_CONFLICT') {
          await onPreviewVersionConflict();
          return;
        }
        setPreviewState({
          phase: 'error',
          signature,
          message: error instanceof Error
            ? error.message
            : '服务端预览暂时不可用',
        });
      } finally {
        if (activePreview.current === controller) {
          activePreview.current = null;
        }
      }
    }, 300);

    return () => {
      window.clearTimeout(timer);
      if (previewSequence.current === sequence) {
        activePreview.current?.abort();
        activePreview.current = null;
      }
    };
  }, [
    decision,
    onPreview,
    onPreviewVersionConflict,
    open,
    order,
    watchedAmount,
    watchedWeight,
  ]);

  useEffect(() => () => {
    previewSequence.current += 1;
    activePreview.current?.abort();
  }, []);

  const updateFromWeight = (value: string) => {
    if (!isCanonicalReviewWeight(value)) {
      form.setFieldValue('targetAmountYuan', undefined);
      setConversion(null);
      return;
    }
    try {
      const next = conversionFromWeight(order, value);
      form.setFieldValue('targetAmountYuan', next.actualAmountYuan);
      setConversion(next);
    } catch {
      form.setFieldValue('targetAmountYuan', undefined);
      setConversion(null);
    }
  };

  const updateFromAmount = (value: string) => {
    if (!isCanonicalReviewAmount(value)) {
      setConversion(null);
      return;
    }
    try {
      if (order.raw.unitPriceYuanPerKg === null) {
        throw new Error('locked unit price is unavailable');
      }
      const next = convertAmountToWeight(
        value,
        order.raw.unitPriceYuanPerKg,
        order.review.maxReviewAbsoluteWeightKg,
      );
      form.setFieldValue('finalWeightKg', next.candidateWeightKg);
      setConversion({ ...next, source: 'amount' });
    } catch {
      setConversion(null);
    }
  };

  const previewVerified = (() => {
    const request = previewRequest(
      order,
      decision,
      watchedWeight?.trim(),
    );
    if (!request || previewState.phase !== 'verified') return false;
    return previewState.signature === previewSignature(
      request,
      watchedAmount?.trim(),
    );
  })();

  return (
    <ModalForm<DeliveryReviewFormValues>
      form={form}
      title={
        kind === 'review'
          ? `审核投递订单 · ${order.deliveryOrderNo}`
          : `纠正投递订单 · ${order.deliveryOrderNo}`
      }
      open={open}
      onOpenChange={onOpenChange}
      initialValues={{
        decision: initialDecision,
        finalWeightKg: seedWeight,
        targetAmountYuan: seedConversion?.actualAmountYuan,
      }}
      modalProps={{
        destroyOnClose: true,
        width: 720,
        maskClosable: false,
      }}
      submitter={{
        searchConfig: {
          submitText: kind === 'review' ? '确认审核' : '确认纠正',
        },
        submitButtonProps: {
          loading: submitting,
          disabled: !previewVerified || submitting,
        },
      }}
      onValuesChange={(changed) => {
        if (Object.prototype.hasOwnProperty.call(changed, 'decision')) {
          invalidatePreview();
          if (changed.decision === 'ORIGINAL_APPROVED') {
            form.setFieldsValue({
              finalWeightKg: undefined,
              targetAmountYuan: undefined,
            });
            setConversion(null);
          } else {
            const nextWeight = form.getFieldValue('finalWeightKg')
              || initialModifiedWeight(kind, order);
            form.setFieldValue('finalWeightKg', nextWeight);
            updateFromWeight(nextWeight ?? '');
          }
          return;
        }
        if (Object.prototype.hasOwnProperty.call(
          changed,
          'finalWeightKg',
        )) {
          invalidatePreview();
          updateFromWeight(changed.finalWeightKg?.trim() ?? '');
          return;
        }
        if (Object.prototype.hasOwnProperty.call(
          changed,
          'targetAmountYuan',
        )) {
          invalidatePreview();
          updateFromAmount(changed.targetAmountYuan?.trim() ?? '');
        }
      }}
      onFinish={async (values) => {
        const finalWeightKg = values.decision === 'MODIFIED_APPROVED'
          ? values.finalWeightKg?.trim()
          : null;
        const request = previewRequest(
          order,
          values.decision,
          finalWeightKg ?? undefined,
        );
        const signature = request
          ? previewSignature(
            request,
            values.targetAmountYuan?.trim(),
          )
          : null;
        if (!request
            || previewState.phase !== 'verified'
            || previewState.signature !== signature) {
          return false;
        }
        return onSubmit({
          expectedRevisionNo: request.expectedRevisionNo,
          decision: request.decision,
          finalWeightKg: request.finalWeightKg,
          reason: values.reason?.trim() || null,
        });
      }}
    >
      <Alert
        showIcon
        type={kind === 'review' ? 'info' : 'warning'}
        message={
          kind === 'review'
            ? '审核会形成第一条认定版本，并按后端计算结果影响钱包'
            : '纠正会追加新版本；设备原始事实和历史认定不会被覆盖'
        }
        style={{ marginBottom: 20 }}
      />

      <Descriptions bordered size="small" column={2}>
        <Descriptions.Item label="当前版本">
          v{order.review.currentRevisionNo}
        </Descriptions.Item>
        <Descriptions.Item label="审核状态">
          <Tag color={order.review.status === 'PENDING' ? 'warning' : 'success'}>
            {order.review.status === 'PENDING' ? '待审核' : '已通过'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="原始重量">
          {order.raw.weightKg === null
            ? '不可用'
            : formatBusinessWeight(order.raw.weightKg)}
        </Descriptions.Item>
        <Descriptions.Item label="原始金额">
          {order.raw.amountYuan === null
            ? '不可用'
            : `¥ ${formatMoneyCny(order.raw.amountYuan)}`}
        </Descriptions.Item>
        <Descriptions.Item label="本单锁定单价">
          {order.raw.unitPriceYuanPerKg === null
            ? '不可用（仅允许认定 0.00 千克）'
            : formatUnitPrice(order.raw.unitPriceYuanPerKg)}
        </Descriptions.Item>
        <Descriptions.Item label="允许绝对值上限">
          ±{formatBusinessWeight(order.review.maxReviewAbsoluteWeightKg)}
        </Descriptions.Item>
      </Descriptions>

      {!rawCanBeApproved && (
        <Alert
          showIcon
          type="warning"
          message="原始重量或金额不可靠，必须填写最终重量"
          description={(
            <>
              当前可靠性：
              <Typography.Text code>
                {order.raw.weightReliability}
                {' / '}
                {order.raw.amountReliability}
              </Typography.Text>
            </>
          )}
          style={{ marginTop: 16 }}
        />
      )}

      <ProFormRadio.Group
        name="decision"
        label={kind === 'review' ? '审核决定' : '纠正方式'}
        rules={[{ required: true }]}
        options={[
          {
            label: kind === 'review' ? '按原始数据通过' : '恢复为原始数据',
            value: 'ORIGINAL_APPROVED',
            disabled: !rawCanBeApproved,
          },
          {
            label: kind === 'review' ? '修改重量后通过' : '重新认定最终重量',
            value: 'MODIFIED_APPROVED',
          },
        ]}
      />

      {decision === 'ORIGINAL_APPROVED' ? (
        <Descriptions size="small" bordered column={2}>
          <Descriptions.Item label="最终认定重量">
            {order.raw.weightKg === null
              ? '不可用'
              : formatBusinessWeight(order.raw.weightKg)}
          </Descriptions.Item>
          <Descriptions.Item label="最终认定金额">
            {order.raw.amountYuan === null
              ? '不可用'
              : `¥ ${formatMoneyCny(order.raw.amountYuan)}`}
          </Descriptions.Item>
        </Descriptions>
      ) : (
        <>
          <ProFormText
            name="finalWeightKg"
            label="最终认定重量（千克）"
            placeholder="例如 1.25 或 -0.50"
            extra={
              `带符号、精确两位小数；范围为 `
              + `±${valueOrDash(order.review.maxReviewAbsoluteWeightKg)} kg。`
            }
            rules={[
              { required: true, message: '请填写最终认定重量' },
              {
                pattern: TWO_DECIMAL_WEIGHT,
                message: '请输入带两位小数的千克字符串',
              },
              {
                validator: async (_, value?: string) => {
                  if (!value || !isCanonicalReviewWeight(value)) return;
                  if (!isWeightWithinAbsoluteLimit(
                    value,
                    order.review.maxReviewAbsoluteWeightKg,
                  )) {
                    throw new Error('最终重量超出本单冻结的审核范围');
                  }
                },
              },
            ]}
          />
          <ProFormText
            name="targetAmountYuan"
            label="目标金额（元，换算辅助）"
            placeholder="例如 1.00 或 -0.50"
            extra="金额只用于反推重量；正式提交仍只有最终重量，后端会重新计算。"
            rules={[
              { required: true, message: '请填写或由重量换算目标金额' },
              {
                pattern: TWO_DECIMAL_MONEY,
                message: '请输入带两位小数的金额字符串',
              },
            ]}
          />
          {conversion && conversion.source === 'amount' && (
            <Alert
              showIcon
              type={conversion.exact ? 'info' : 'warning'}
              message={conversion.exact
                ? '目标金额可以由候选重量实现'
                : '目标金额无法由两位小数重量精确实现'}
              description={(
                <>
                  目标金额 ¥ {formatMoneyCny(conversion.targetAmountYuan)}；
                  候选重量 {formatBusinessWeight(conversion.candidateWeightKg)}；
                  实际金额 ¥ {formatMoneyCny(conversion.actualAmountYuan)}。
                  {conversion.realizingCandidateCount !== '0'
                    && conversion.realizingCandidateCount !== '1'
                    ? ` 同一目标金额共有 ${conversion.realizingCandidateCount} 个可实现重量，范围 ${conversion.realizingWeightMinKg}～${conversion.realizingWeightMaxKg} 千克。`
                    : ''}
                </>
              )}
              style={{ marginBottom: 16 }}
            />
          )}
          {conversion && conversion.source === 'weight' && (
            <Alert
              showIcon
              type="info"
              message="已按本单锁定单价换算预计金额"
              description={(
                <>
                  {formatBusinessWeight(conversion.candidateWeightKg)} 对应
                  金额 ¥ {formatMoneyCny(conversion.actualAmountYuan)}。
                </>
              )}
              style={{ marginBottom: 16 }}
            />
          )}
        </>
      )}

      {(previewState.phase === 'waiting'
        || previewState.phase === 'loading') && (
        <Alert
          showIcon
          type="info"
          message={previewState.phase === 'waiting'
            ? '等待输入稳定后核对服务端预览'
            : '正在核对服务端预览'}
          style={{ marginBottom: 16 }}
        />
      )}
      {previewState.phase === 'verified' && (
        <Alert
          showIcon
          type="success"
          message="客户端换算与服务端预览一致，可以确认"
          description={(
            <>
              最终金额 ¥ {formatMoneyCny(
                previewState.server.finalAmountYuan,
              )}；钱包差额 ¥ {formatMoneyCny(
                previewState.server.walletDeltaYuan,
              )}。
            </>
          )}
          style={{ marginBottom: 16 }}
        />
      )}
      {previewState.phase === 'mismatch' && (
        <Alert
          showIcon
          type="error"
          message="客户端换算与服务端预览不一致，已禁止确认"
          description="请刷新订单；正式审核不会信任客户端金额。"
          style={{ marginBottom: 16 }}
        />
      )}
      {previewState.phase === 'error' && (
        <Alert
          showIcon
          type="error"
          message="服务端预览失败，已禁止确认"
          description={previewState.message}
          style={{ marginBottom: 16 }}
        />
      )}

      <ProFormTextArea
        name="reason"
        label="说明"
        placeholder={
          kind === 'review'
            ? '选填：记录现场核对依据'
            : '选填：说明本次纠正依据'
        }
        fieldProps={{ maxLength: 500, showCount: true, rows: 3 }}
      />
    </ModalForm>
  );
}

export default function DeliveryReviewModal(
  props: DeliveryReviewModalProps,
) {
  if (!props.order) return null;
  return (
    <DeliveryReviewModalForm
      key={`${props.kind}-${props.order.deliveryOrderNo}-${props.order.review.currentRevisionNo}`}
      {...props}
      order={props.order}
    />
  );
}
