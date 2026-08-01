import {
  AuditOutlined,
  EditOutlined,
  ExclamationCircleOutlined,
  FileImageOutlined,
} from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Image,
  Row,
  Skeleton,
  Space,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import type { DeliveryOrderDetail } from '@/api/deliveryOrders';
import {
  formatBusinessWeight,
  formatMoneyCny,
  formatShanghaiTime,
  formatUnitPrice,
} from '@/utils/decimal';

interface DeliveryOrderDetailDrawerProps {
  open: boolean;
  loading: boolean;
  order: DeliveryOrderDetail | null;
  canReview: boolean;
  canCorrect: boolean;
  onClose: () => void;
  onReview: () => void;
  onCorrect: () => void;
}

const photoPositionLabels: Record<string, string> = {
  BEFORE_INNER: '投递前 · 箱内',
  BEFORE_OUTER: '投递前 · 箱外',
  AFTER_INNER: '投递后 · 箱内',
  AFTER_OUTER: '投递后 · 箱外',
};

const photoStatusLabels: Record<string, string> = {
  UPLOAD_PENDING: '上传中',
  AVAILABLE: '可查看',
  PERMANENTLY_MISSING: '永久缺失',
};

const missingReasonLabels: Record<string, string> = {
  DEVICE_DID_NOT_PRODUCE_PHOTO: '设备未产生照片',
  PHOTO_CAPTURE_FAILED: '拍摄失败',
  UPLOAD_FAILED_PERMANENTLY: '上传最终失败',
  PHOTO_UNAVAILABLE: '照片不可用',
};

const reviewerKindLabels: Record<string, string> = {
  PLATFORM_ADMIN: '平台管理员',
  TENANT_PRINCIPAL: '租户主体',
  STAFF_ACCOUNT: '工作人员',
};

function reviewStatusTag(status: string) {
  return status === 'PENDING'
    ? <Tag color="warning">待审核</Tag>
    : <Tag color="success">已通过</Tag>;
}

function reliabilityTag(reliability: string) {
  const label: Record<string, string> = {
    RELIABLE: '可靠',
    INVALID: '超出范围',
    MISSING: '缺失',
    INCONSISTENT: '不一致',
    WEIGHT_UNRELIABLE: '重量不可靠',
  };
  return (
    <Tag color={reliability === 'RELIABLE' ? 'success' : 'warning'}>
      {label[reliability] ?? reliability}
    </Tag>
  );
}

function weight(value: string | null): string {
  return value === null ? '—' : formatBusinessWeight(value);
}

function money(value: string | null): string {
  return value === null ? '—' : `¥ ${formatMoneyCny(value)}`;
}

function grams(value: number | null): string {
  return value === null ? '—' : `${value.toLocaleString('zh-CN')} g`;
}

export default function DeliveryOrderDetailDrawer({
  open,
  loading,
  order,
  canReview,
  canCorrect,
  onClose,
  onReview,
  onCorrect,
}: DeliveryOrderDetailDrawerProps) {
  const reviewAvailable =
    !!order
    && canReview
    && order.review.status === 'PENDING'
    && order.review.currentRevisionNo === 0;
  const correctionAvailable =
    !!order
    && canCorrect
    && order.review.status === 'APPROVED';

  return (
    <Drawer
      title={(
        <Space wrap>
          <span>投递订单详情</span>
          {order && (
            <>
              <Typography.Text code>{order.deliveryOrderNo}</Typography.Text>
              {reviewStatusTag(order.review.status)}
            </>
          )}
        </Space>
      )}
      width="min(920px, calc(100vw - 24px))"
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={(
        <Space>
          {reviewAvailable && (
            <Button
              type="primary"
              icon={<AuditOutlined />}
              aria-label="审核"
              onClick={onReview}
            >
              审核
            </Button>
          )}
          {correctionAvailable && (
            <Button
              icon={<EditOutlined />}
              aria-label="纠正"
              onClick={onCorrect}
            >
              纠正
            </Button>
          )}
        </Space>
      )}
    >
      {loading || !order ? (
        <Skeleton active paragraph={{ rows: 12 }} />
      ) : (
        <Space
          direction="vertical"
          size={16}
          style={{ display: 'flex', width: '100%' }}
        >
          {(order.raw.negativeWeightAnomaly || order.anomalies.length > 0) && (
            <Alert
              showIcon
              type="warning"
              icon={<ExclamationCircleOutlined />}
              message="该订单包含需要人工核对的事实"
              description={
                order.raw.negativeWeightAnomaly
                  ? '设备锁存了投递过程中达到阈值的重量减少。该标志不会自动修改订单重量或钱包。'
                  : '请在审核或纠正前检查异常记录、重量状态和照片证据。'
              }
            />
          )}

          <Card size="small" title="当前认定">
            <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small">
              <Descriptions.Item label="状态">
                {reviewStatusTag(order.review.status)}
              </Descriptions.Item>
              <Descriptions.Item label="当前版本">
                v{order.review.currentRevisionNo}
              </Descriptions.Item>
              <Descriptions.Item label="首次通过">
                {order.review.firstApprovedAt
                  ? formatShanghaiTime(order.review.firstApprovedAt)
                  : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="最终重量">
                {weight(order.review.finalWeightKg)}
              </Descriptions.Item>
              <Descriptions.Item label="最终金额">
                {money(order.review.finalAmountYuan)}
              </Descriptions.Item>
              <Descriptions.Item label="人工认定上限">
                ±{formatBusinessWeight(order.review.maxReviewAbsoluteWeightKg)}
              </Descriptions.Item>
            </Descriptions>
          </Card>

          <Card size="small" title="设备原始事实">
            <Descriptions
              bordered
              column={{ xs: 1, sm: 2 }}
              size="small"
            >
              <Descriptions.Item label="设备 / 投口">
                {order.source.deploymentCode} / {order.source.portNo}
              </Descriptions.Item>
              <Descriptions.Item label="机构用户">
                <Typography.Text copyable>
                  {order.ownership.organizationUserUid}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="设备发生时间">
                {order.source.deviceOccurredAt
                  ? formatShanghaiTime(order.source.deviceOccurredAt)
                  : '设备时间缺失'}
              </Descriptions.Item>
              <Descriptions.Item label="后端接收时间">
                {formatShanghaiTime(order.source.receivedAt)}
              </Descriptions.Item>
              <Descriptions.Item label="开门前重量">
                {grams(order.raw.firstPreOpenWeightGram)}
              </Descriptions.Item>
              <Descriptions.Item label="关门后重量">
                {grams(order.raw.finalPostCloseWeightGram)}
              </Descriptions.Item>
              <Descriptions.Item label="净重量克值">
                {grams(order.raw.netWeightGram)}
              </Descriptions.Item>
              <Descriptions.Item label="原始业务重量">
                <Space>
                  {weight(order.raw.weightKg)}
                  {reliabilityTag(order.raw.weightReliability)}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="锁定单价">
                {order.raw.unitPriceYuanPerKg
                  ? formatUnitPrice(order.raw.unitPriceYuanPerKg)
                  : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="原始金额">
                <Space>
                  {money(order.raw.amountYuan)}
                  {reliabilityTag(order.raw.amountReliability)}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="负重量异常">
                <Tag
                  color={order.raw.negativeWeightAnomaly ? 'warning' : 'default'}
                >
                  {order.raw.negativeWeightAnomaly ? '已锁存' : '未检测到'}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="事件 UID">
                <Typography.Text copyable>
                  {order.source.eventUid}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="会话 UID">
                <Typography.Text copyable>
                  {order.source.sessionUid}
                </Typography.Text>
              </Descriptions.Item>
            </Descriptions>
          </Card>

          <Card
            size="small"
            title={`异常记录 · ${order.anomalies.length}`}
          >
            {order.anomalies.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="没有异常记录"
              />
            ) : (
              <Space
                direction="vertical"
                size={10}
                style={{ display: 'flex' }}
              >
                {order.anomalies.map((anomaly) => (
                  <Alert
                    key={`${anomaly.code}-${anomaly.detectedAt}`}
                    showIcon
                    type="warning"
                    message={(
                      <Space wrap>
                        <Tag color={anomaly.category === 'USER' ? 'gold' : 'red'}>
                          {anomaly.category === 'USER' ? '用户行为' : '系统事实'}
                        </Tag>
                        <Typography.Text code>{anomaly.code}</Typography.Text>
                      </Space>
                    )}
                    description={(
                      <>
                        <div>{anomaly.message}</div>
                        <Typography.Text type="secondary">
                          {formatShanghaiTime(anomaly.detectedAt)}
                        </Typography.Text>
                        {anomaly.diagnosticDetails && (
                          <pre className="delivery-diagnostic">
                            {JSON.stringify(
                              anomaly.diagnosticDetails,
                              null,
                              2,
                            )}
                          </pre>
                        )}
                      </>
                    )}
                  />
                ))}
              </Space>
            )}
          </Card>

          <Card
            size="small"
            title={(
              <Space>
                <FileImageOutlined />
                <span>照片证据</span>
              </Space>
            )}
          >
            <Image.PreviewGroup>
              <Row gutter={[12, 12]}>
                {order.photos.map((photo) => (
                  <Col xs={24} sm={12} key={photo.position}>
                    <Card
                      size="small"
                      type="inner"
                      title={photoPositionLabels[photo.position] ?? photo.position}
                      extra={(
                        <Tag
                          color={
                            photo.status === 'AVAILABLE'
                              ? 'success'
                              : photo.status === 'UPLOAD_PENDING'
                                ? 'processing'
                                : 'default'
                          }
                        >
                          {photoStatusLabels[photo.status] ?? photo.status}
                        </Tag>
                      )}
                    >
                      {photo.url ? (
                        <Image
                          src={photo.url}
                          alt={photoPositionLabels[photo.position]}
                          width="100%"
                          height={180}
                          style={{ objectFit: 'cover', borderRadius: 6 }}
                        />
                      ) : (
                        <div className="delivery-photo-placeholder">
                          <FileImageOutlined />
                          <span>
                            {photo.status === 'UPLOAD_PENDING'
                              ? '设备仍在上传'
                              : missingReasonLabels[
                                  photo.missingReason ?? ''
                                ] ?? '照片不可用'}
                          </span>
                        </div>
                      )}
                      <Typography.Text type="secondary">
                        拍摄时间：
                        {photo.capturedAt
                          ? formatShanghaiTime(photo.capturedAt)
                          : '未提供'}
                      </Typography.Text>
                    </Card>
                  </Col>
                ))}
              </Row>
            </Image.PreviewGroup>
          </Card>

          <Card
            size="small"
            title={`认定记录 · ${order.revisions.length}`}
          >
            {order.revisions.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="尚无审核记录"
              />
            ) : (
              <Timeline
                items={order.revisions
                  .slice()
                  .reverse()
                  .map((revision) => ({
                    color:
                      revision.revisionType === 'CORRECTION'
                        ? 'orange'
                        : 'green',
                    children: (
                      <div>
                        <Space wrap>
                          <Typography.Text strong>
                            v{revision.revisionNo}
                            {' · '}
                            {revision.revisionType === 'INITIAL_REVIEW'
                              ? '首次审核'
                              : '纠正'}
                          </Typography.Text>
                          <Tag>
                            {revision.decision === 'ORIGINAL_APPROVED'
                              ? '按原始数据'
                              : '修改重量'}
                          </Tag>
                        </Space>
                        <div>
                          {weight(revision.beforeFinalWeightKg)}
                          {' → '}
                          {weight(revision.afterFinalWeightKg)}
                          {'；金额 '}
                          {money(revision.beforeFinalAmountYuan)}
                          {' → '}
                          {money(revision.afterFinalAmountYuan)}
                          {'；钱包差额 '}
                          {money(revision.amountDeltaYuan)}
                        </div>
                        <Typography.Text type="secondary">
                          {reviewerKindLabels[revision.operator.actorKind]
                            ?? revision.operator.actorKind}
                          {' · '}
                          {revision.operator.displayName}
                          {' · '}
                          {formatShanghaiTime(revision.reviewedAt)}
                        </Typography.Text>
                        {revision.reason && (
                          <Typography.Paragraph style={{ margin: '6px 0 0' }}>
                            说明：{revision.reason}
                          </Typography.Paragraph>
                        )}
                      </div>
                    ),
                  }))}
              />
            )}
          </Card>
        </Space>
      )}
    </Drawer>
  );
}
