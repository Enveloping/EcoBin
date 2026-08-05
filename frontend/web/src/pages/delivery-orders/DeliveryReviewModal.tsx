import {
  ModalForm,
  ProFormDependency,
  ProFormRadio,
  ProFormText,
  ProFormTextArea,
} from '@ant-design/pro-components';
import { Alert, Descriptions, Tag, Typography } from 'antd';
import type {
  DeliveryOrderDetail,
  DeliveryReviewDecision,
  DeliveryReviewRequest,
} from '@/api/deliveryOrders';
import {
  formatBusinessWeight,
  formatMoneyCny,
} from '@/utils/decimal';

export type DeliveryMutationKind = 'review' | 'correction';

interface DeliveryReviewFormValues {
  decision: DeliveryReviewDecision;
  finalWeightKg?: string;
  reason?: string;
}

interface DeliveryReviewModalProps {
  kind: DeliveryMutationKind;
  order: DeliveryOrderDetail | null;
  open: boolean;
  submitting: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (request: DeliveryReviewRequest) => Promise<boolean>;
}

const TWO_DECIMAL_WEIGHT = /^-?(0|[1-9]\d*)\.\d{2}$/;

function valueOrDash(value: string | null): string {
  return value === null ? '—' : value;
}

export default function DeliveryReviewModal({
  kind,
  order,
  open,
  submitting,
  onOpenChange,
  onSubmit,
}: DeliveryReviewModalProps) {
  if (!order) return null;

  const rawCanBeApproved =
    order.raw.weightReliability === 'RELIABLE'
    && order.raw.amountReliability === 'RELIABLE'
    && order.raw.weightKg !== null
    && order.raw.amountYuan !== null;
  const initialDecision: DeliveryReviewDecision =
    kind === 'correction' || !rawCanBeApproved
      ? 'MODIFIED_APPROVED'
      : 'ORIGINAL_APPROVED';

  return (
    <ModalForm<DeliveryReviewFormValues>
      key={`${kind}-${order.deliveryOrderNo}-${order.review.currentRevisionNo}`}
      title={
        kind === 'review'
          ? `审核投递订单 · ${order.deliveryOrderNo}`
          : `纠正投递订单 · ${order.deliveryOrderNo}`
      }
      open={open}
      onOpenChange={onOpenChange}
      initialValues={{
        decision: initialDecision,
        finalWeightKg:
          kind === 'correction'
            ? order.review.finalWeightKg ?? order.raw.weightKg ?? undefined
            : undefined,
      }}
      modalProps={{
        destroyOnClose: true,
        width: 680,
        maskClosable: false,
      }}
      submitter={{
        searchConfig: {
          submitText: kind === 'review' ? '确认审核' : '确认纠正',
        },
        submitButtonProps: {
          loading: submitting,
        },
      }}
      onFinish={async (values) => {
        const finalWeightKg =
          values.decision === 'MODIFIED_APPROVED'
            ? values.finalWeightKg?.trim()
            : null;
        return onSubmit({
          expectedRevisionNo: order.review.currentRevisionNo,
          decision: values.decision,
          finalWeightKg,
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
        <Descriptions.Item label="当前认定重量">
          {order.review.finalWeightKg === null
            ? '尚未认定'
            : formatBusinessWeight(order.review.finalWeightKg)}
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
          description={
            <>
              当前可靠性：
              <Typography.Text code>
                {order.raw.weightReliability}
                {' / '}
                {order.raw.amountReliability}
              </Typography.Text>
            </>
          }
          style={{ marginTop: 16 }}
        />
      )}

      <ProFormRadio.Group
        name="decision"
        label={kind === 'review' ? '审核决定' : '纠正方式'}
        rules={[{ required: true }]}
        options={[
          {
            label:
              kind === 'review'
                ? '按原始数据通过'
                : '恢复为原始数据',
            value: 'ORIGINAL_APPROVED',
            disabled: !rawCanBeApproved,
          },
          {
            label:
              kind === 'review'
                ? '修改重量后通过'
                : '重新认定最终重量',
            value: 'MODIFIED_APPROVED',
          },
        ]}
      />

      <ProFormDependency name={['decision']}>
        {({ decision }: { decision?: DeliveryReviewDecision }) =>
          decision === 'MODIFIED_APPROVED' ? (
            <ProFormText
              name="finalWeightKg"
              label="最终认定重量（千克）"
              placeholder="例如 1.25 或 -0.50"
              extra={
                `带符号、精确两位小数；范围为 `
                + `±${valueOrDash(order.review.maxReviewAbsoluteWeightKg)} kg。`
                + '最终金额由后端按本单锁定单价计算。'
              }
              rules={[
                { required: true, message: '请填写最终认定重量' },
                {
                  pattern: TWO_DECIMAL_WEIGHT,
                  message: '请输入带两位小数的千克字符串',
                },
              ]}
            />
          ) : null
        }
      </ProFormDependency>

      <ProFormTextArea
        name="reason"
        label="说明（用户可见）"
        placeholder={
          kind === 'review'
            ? '选填：向用户说明本次审核或现场核对依据'
            : '选填：向用户说明本次重量或金额纠正依据'
        }
        fieldProps={{ maxLength: 500, showCount: true, rows: 3 }}
      />
    </ModalForm>
  );
}
