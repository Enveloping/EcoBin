import { useCallback, useEffect, useRef, useState } from 'react';
import {
  EyeOutlined,
  PictureOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  App,
  Button,
  Empty,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { useSearchParams } from 'react-router-dom';
import {
  correctDeliveryOrder,
  getDeliveryOrder,
  listDeliveryOrders,
  reviewDeliveryOrder,
  type DeliveryOrderDetail,
  type DeliveryOrderItem,
  type DeliveryPhotoCompleteness,
  type DeliveryReviewRequest,
  type DeliveryReviewStatus,
} from '@/api/deliveryOrders';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { useAuthStore } from '@/stores/authStore';
import {
  formatBusinessWeight,
  formatMoneyCny,
  formatShanghaiTime,
} from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import DeliveryOrderDetailDrawer from './DeliveryOrderDetailDrawer';
import DeliveryReviewModal, {
  type DeliveryMutationKind,
} from './DeliveryReviewModal';

interface DeliverySearchParams {
  reviewStatus?: DeliveryReviewStatus;
  occurredRange?: [string, string];
  organizationUserUid?: string;
  deploymentCode?: string;
  portNo?: number;
  anomalyCode?: string;
  photoCompleteness?: DeliveryPhotoCompleteness;
}

interface PageSnapshot {
  page: number;
  itemCount: number;
  asOf: string;
}

const USER_UID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const weightReliabilityLabels: Record<string, string> = {
  RELIABLE: '可靠',
  INVALID: '超出范围',
  MISSING: '缺失',
  INCONSISTENT: '不一致',
};

function requestErrorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '投递订单加载失败';
}

function weightCell(order: DeliveryOrderItem) {
  const value = order.reviewStatus === 'APPROVED'
    ? order.finalWeightKg
    : order.rawWeightKg;
  return (
    <Space direction="vertical" size={2}>
      <Typography.Text strong={value !== null}>
        {value === null ? '不可用' : formatBusinessWeight(value)}
      </Typography.Text>
      <Tag
        color={
          order.reviewStatus === 'APPROVED'
            ? 'success'
            : order.rawWeightReliability === 'RELIABLE'
              ? 'default'
              : 'warning'
        }
      >
        {order.reviewStatus === 'APPROVED'
          ? '最终认定'
          : `原始${weightReliabilityLabels[order.rawWeightReliability] ?? order.rawWeightReliability}`}
      </Tag>
    </Space>
  );
}

function amountCell(order: DeliveryOrderItem) {
  const value = order.reviewStatus === 'APPROVED'
    ? order.finalAmountYuan
    : order.rawAmountYuan;
  return (
    <Space direction="vertical" size={2}>
      <Typography.Text strong={value !== null}>
        {value === null ? '不可用' : `¥ ${formatMoneyCny(value)}`}
      </Typography.Text>
      <Typography.Text type="secondary">
        {order.reviewStatus === 'APPROVED' ? '已入认定' : '尚未进入钱包'}
      </Typography.Text>
    </Space>
  );
}

export default function DeliveryOrdersPage() {
  const scope = useDirectoryScope();
  const organizationScope = useOrganizationScope(scope);
  const [searchParams] = useSearchParams();
  const { message } = App.useApp();
  const actionRef = useRef<ActionType>(null);
  const cursorByPage = useRef<Map<number, string | undefined>>(
    new Map([[1, undefined]]),
  );
  const detailRequestSequence = useRef(0);
  const selectedDetailOrderNo = useRef<string | null>(null);
  const executeCommand = useCommandExecutor();
  const canReadAll = useAuthStore((state) =>
    state.hasCapability('delivery.read'));
  const canReview = useAuthStore((state) =>
    state.hasCapability('review.execute'));
  const canCorrect = useAuthStore((state) =>
    state.hasCapability('delivery.correct'));
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [paginationTotal, setPaginationTotal] = useState(0);
  const [pageSnapshot, setPageSnapshot] = useState<PageSnapshot | null>(null);
  const [tableError, setTableError] = useState<string>();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail] = useState<DeliveryOrderDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [mutationKind, setMutationKind] =
    useState<DeliveryMutationKind | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const organizationCode = organizationScope.organizationCode;
  const linkedOrganizationUserUid =
    searchParams.get('organizationUserUid')?.trim() || undefined;
  const linkedDeploymentCode =
    searchParams.get('deploymentCode')?.trim() || undefined;

  const resetCursorNavigation = useCallback(() => {
    cursorByPage.current = new Map([[1, undefined]]);
    setCurrentPage(1);
    setPaginationTotal(0);
    setPageSnapshot(null);
  }, []);

  useEffect(() => {
    detailRequestSequence.current += 1;
    selectedDetailOrderNo.current = null;
    resetCursorNavigation();
    setTableError(undefined);
    setDrawerOpen(false);
    setDetail(null);
    setDetailLoading(false);
    setMutationKind(null);
    return () => {
      detailRequestSequence.current += 1;
      selectedDetailOrderNo.current = null;
    };
  }, [organizationCode, resetCursorNavigation, scope.context]);

  const loadDetail = useCallback(
    async (deliveryOrderNo: string) => {
      if (
        !scope.context
        || !organizationCode
        || selectedDetailOrderNo.current !== deliveryOrderNo
      ) {
        return null;
      }
      const requestSequence = ++detailRequestSequence.current;
      setDetailLoading(true);
      try {
        const loaded = await getDeliveryOrder(
          scope.context,
          organizationCode,
          deliveryOrderNo,
        );
        if (
          detailRequestSequence.current !== requestSequence
          || selectedDetailOrderNo.current !== deliveryOrderNo
        ) {
          return null;
        }
        if (loaded.deliveryOrderNo !== deliveryOrderNo) {
          throw new Error('投递订单详情响应与当前请求不一致');
        }
        setDetail(loaded);
        return loaded;
      } finally {
        if (
          detailRequestSequence.current === requestSequence
          && selectedDetailOrderNo.current === deliveryOrderNo
        ) {
          setDetailLoading(false);
        }
      }
    },
    [organizationCode, scope.context],
  );

  const openDetail = async (order: DeliveryOrderItem) => {
    selectedDetailOrderNo.current = order.deliveryOrderNo;
    setDrawerOpen(true);
    setDetail(null);
    try {
      await loadDetail(order.deliveryOrderNo);
    } catch {
      if (selectedDetailOrderNo.current === order.deliveryOrderNo) {
        detailRequestSequence.current += 1;
        selectedDetailOrderNo.current = null;
        setDrawerOpen(false);
        setDetail(null);
        setDetailLoading(false);
      }
    }
  };

  const closeDetail = () => {
    detailRequestSequence.current += 1;
    selectedDetailOrderNo.current = null;
    setDrawerOpen(false);
    setDetail(null);
    setDetailLoading(false);
    setMutationKind(null);
  };

  const submitReview = async (
    request: DeliveryReviewRequest,
  ): Promise<boolean> => {
    if (
      !detail
      || selectedDetailOrderNo.current !== detail.deliveryOrderNo
      || !scope.context
      || !organizationCode
      || !mutationKind
    ) {
      return false;
    }
    const orderNo = detail.deliveryOrderNo;
    setSubmitting(true);
    try {
      const result = await executeCommand(
        commandKey(
          mutationKind === 'review'
            ? 'review-delivery-order'
            : 'correct-delivery-order',
          orderNo,
          request,
        ),
        (intent) =>
          mutationKind === 'review'
            ? reviewDeliveryOrder(
                scope.context!,
                organizationCode,
                orderNo,
                request,
                intent,
              )
            : correctDeliveryOrder(
                scope.context!,
                organizationCode,
                orderNo,
                request,
                intent,
              ),
      );
      message.success(
        `${mutationKind === 'review' ? '审核' : '纠正'}已提交：`
        + `最终金额 ¥ ${formatMoneyCny(result.finalAmountYuan)}，`
        + `钱包差额 ¥ ${formatMoneyCny(result.walletDeltaYuan)}`,
      );
      setMutationKind(null);
      if (selectedDetailOrderNo.current === orderNo) {
        await loadDetail(orderNo);
      }
      actionRef.current?.reload();
      return true;
    } catch (error) {
      if (error instanceof ApiProblem && error.status === 409) {
        setMutationKind(null);
        try {
          if (selectedDetailOrderNo.current === orderNo) {
            await loadDetail(orderNo);
          }
          actionRef.current?.reload();
        } finally {
          message.warning('订单版本已经变化，已载入最新认定记录；请重新核对');
        }
      }
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  const columns: ProColumns<DeliveryOrderItem>[] = [
    {
      title: '订单号',
      dataIndex: 'deliveryOrderNo',
      search: false,
      width: 190,
      fixed: 'left',
      render: (_, order) => (
        <Button
          type="link"
          className="table-link"
          style={{ padding: 0 }}
          onClick={() => openDetail(order)}
        >
          {order.deliveryOrderNo}
        </Button>
      ),
    },
    {
      title: '机构用户 UID',
      dataIndex: 'organizationUserUid',
      width: 210,
      fieldProps: {
        placeholder: '输入完整用户 UID',
      },
      formItemProps: {
        rules: [
          {
            pattern: USER_UID,
            message: '请输入 UUIDv4 格式的机构用户 UID',
          },
        ],
      },
      render: (_, order) => (
        <Typography.Text copyable ellipsis>
          {order.organizationUserUid}
        </Typography.Text>
      ),
    },
    {
      title: '设备部署',
      dataIndex: 'deploymentCode',
      width: 170,
      fieldProps: {
        placeholder: '输入部署编码',
      },
      render: (_, order) => (
        <Space direction="vertical" size={1}>
          <Typography.Text code>{order.deploymentCode}</Typography.Text>
          <Typography.Text type="secondary">
            投口 {order.portNo}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '投口',
      dataIndex: 'portNo',
      valueType: 'select',
      hideInTable: true,
      fieldProps: {
        options: Array.from({ length: 6 }, (_, index) => ({
          label: `投口 ${index + 1}`,
          value: index + 1,
        })),
      },
    },
    {
      title: '审核状态',
      dataIndex: 'reviewStatus',
      valueType: 'select',
      width: 130,
      hideInSearch: !canReadAll,
      valueEnum: {
        PENDING: { text: '待审核' },
        APPROVED: { text: '已通过' },
      },
      render: (_, order) => (
        <Space direction="vertical" size={2}>
          <Tag color={order.reviewStatus === 'PENDING' ? 'warning' : 'success'}>
            {order.reviewStatus === 'PENDING' ? '待审核' : '已通过'}
          </Tag>
          <Typography.Text type="secondary">
            v{order.currentRevisionNo}
            {order.currentRevisionNo >= 2 ? ' · 已纠正' : ''}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '重量',
      key: 'weight',
      search: false,
      width: 150,
      render: (_, order) => weightCell(order),
    },
    {
      title: '金额',
      key: 'amount',
      search: false,
      width: 150,
      render: (_, order) => amountCell(order),
    },
    {
      title: '证据',
      key: 'evidence',
      search: false,
      width: 160,
      render: (_, order) => (
        <Space direction="vertical" size={4}>
          <Tag
            icon={<PictureOutlined />}
            color={
              order.photoCompleteness === 'COMPLETE'
                ? 'success'
                : 'warning'
            }
          >
            {order.photoCompleteness === 'COMPLETE'
              ? '四图完整'
              : '照片不完整'}
          </Tag>
          {order.anomalyCodes.length ? (
            <Tag icon={<WarningOutlined />} color="warning">
              {order.anomalyCodes.length} 项异常
            </Tag>
          ) : (
            <Typography.Text type="secondary">无异常</Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '照片完整性',
      dataIndex: 'photoCompleteness',
      valueType: 'select',
      hideInTable: true,
      valueEnum: {
        COMPLETE: { text: '四图完整' },
        INCOMPLETE: { text: '照片不完整' },
      },
    },
    {
      title: '异常代码',
      dataIndex: 'anomalyCode',
      hideInTable: true,
      fieldProps: {
        placeholder: '例如 NEGATIVE_WEIGHT_ANOMALY',
      },
    },
    {
      title: '发生时间',
      dataIndex: 'deviceOccurredAt',
      search: false,
      width: 180,
      render: (_, order) => (
        <Space direction="vertical" size={1}>
          <Typography.Text>
            {order.deviceOccurredAt
              ? formatShanghaiTime(order.deviceOccurredAt)
              : '设备时间缺失'}
          </Typography.Text>
          <Typography.Text type="secondary">
            接收 {formatShanghaiTime(order.receivedAt)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '发生时间范围',
      dataIndex: 'occurredRange',
      valueType: 'dateTimeRange',
      hideInTable: true,
    },
    {
      title: '操作',
      key: 'operation',
      valueType: 'option',
      width: 90,
      fixed: 'right',
      hideInSetting: true,
      render: (_, order) => [
        <Button
          key="detail"
          type="link"
          size="small"
          icon={<EyeOutlined />}
          onClick={() => openDetail(order)}
        >
          详情
        </Button>,
      ],
    },
  ];

  const content = (() => {
    if (scope.loading || organizationScope.loading) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在加载投递订单范围" />
        </div>
      );
    }
    if (!scope.context) {
      return <Empty description="请选择目标租户" />;
    }
    if (!organizationScope.organizationOptions.length) {
      return <Empty description="当前租户尚无机构" />;
    }
    if (!organizationCode) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在应用机构范围" />
        </div>
      );
    }

    return (
      <>
        {!canReadAll && canReview && (
          <Alert
            showIcon
            type="info"
            message="当前账号仅具有投递审核权限"
            description="列表由服务端固定为待审核订单；已通过历史和纠正记录不会通过该权限暴露。"
            style={{ marginBottom: 16 }}
          />
        )}
        {tableError && (
          <Alert
            showIcon
            type="error"
            message="投递订单加载失败"
            description={tableError}
            style={{ marginBottom: 16 }}
          />
        )}
        <ProTable<DeliveryOrderItem, DeliverySearchParams>
          {...proTableConfig}
          actionRef={actionRef}
          rowKey="deliveryOrderNo"
          columns={columns}
          scroll={{ x: 1460 }}
          columnsState={{
            persistenceKey: 'ecobin.web.columns.delivery-orders.v1',
            persistenceType: 'localStorage',
          }}
          form={{
            initialValues: {
              organizationUserUid: linkedOrganizationUserUid,
              deploymentCode: linkedDeploymentCode,
              reviewStatus: canReadAll ? undefined : 'PENDING',
            },
          }}
          params={{
            organizationUserUid: linkedOrganizationUserUid,
            deploymentCode: linkedDeploymentCode,
          }}
          beforeSearchSubmit={(params) => {
            resetCursorNavigation();
            return params;
          }}
          headerTitle={(
            <Space wrap>
              <Typography.Text strong>目标机构</Typography.Text>
              <Select
                aria-label="目标机构"
                showSearch
                optionFilterProp="label"
                style={{ width: 340 }}
                value={organizationCode}
                options={organizationScope.organizationOptions}
                onChange={organizationScope.setOrganizationCode}
              />
              {pageSnapshot && (
                <Typography.Text type="secondary">
                  数据快照 {formatShanghaiTime(pageSnapshot.asOf)}
                  {' · '}
                  第 {pageSnapshot.page} 页，本页 {pageSnapshot.itemCount} 条
                </Typography.Text>
              )}
            </Space>
          )}
          request={async (params) => {
            if (!scope.context || !organizationCode) {
              return { data: [], total: 0, success: true };
            }
            const requestedPage = params.current ?? currentPage;
            const requestedLimit = params.pageSize ?? pageSize;
            const cursor = cursorByPage.current.get(requestedPage);
            if (requestedPage > 1 && !cursor) {
              return { data: [], total: 0, success: false };
            }
            const occurredRange = params.occurredRange as
              | [string, string]
              | undefined;
            try {
              setTableError(undefined);
              const page = await listDeliveryOrders(
                scope.context,
                organizationCode,
                {
                  cursor,
                  limit: requestedLimit,
                  reviewStatus: canReadAll
                    ? params.reviewStatus as DeliveryReviewStatus | undefined
                    : undefined,
                  occurredFrom: occurredRange?.[0]
                    ? dayjs(occurredRange[0]).toISOString()
                    : undefined,
                  occurredTo: occurredRange?.[1]
                    ? dayjs(occurredRange[1]).toISOString()
                    : undefined,
                  organizationUserUid:
                    typeof params.organizationUserUid === 'string'
                      ? params.organizationUserUid.trim() || undefined
                      : undefined,
                  deploymentCode:
                    typeof params.deploymentCode === 'string'
                      ? params.deploymentCode.trim() || undefined
                      : undefined,
                  portNo: params.portNo === undefined
                    ? undefined
                    : Number(params.portNo),
                  anomalyCode:
                    typeof params.anomalyCode === 'string'
                      ? params.anomalyCode.trim() || undefined
                      : undefined,
                  photoCompleteness:
                    params.photoCompleteness as
                      | DeliveryPhotoCompleteness
                      | undefined,
                },
              );

              for (const pageNumber of cursorByPage.current.keys()) {
                if (pageNumber > requestedPage + 1) {
                  cursorByPage.current.delete(pageNumber);
                }
              }
              if (page.nextCursor) {
                cursorByPage.current.set(
                  requestedPage + 1,
                  page.nextCursor,
                );
              } else {
                cursorByPage.current.delete(requestedPage + 1);
              }
              const total = page.nextCursor
                ? requestedPage * requestedLimit + 1
                : (requestedPage - 1) * requestedLimit + page.items.length;
              setPaginationTotal(total);
              setPageSnapshot({
                page: requestedPage,
                itemCount: page.items.length,
                asOf: page.asOf,
              });
              return {
                data: page.items,
                total,
                success: true,
              };
            } catch (error) {
              setTableError(requestErrorMessage(error));
              return { data: [], total: 0, success: false };
            }
          }}
          pagination={{
            current: currentPage,
            pageSize,
            total: paginationTotal,
            showQuickJumper: false,
            pageSizeOptions: [20, 50, 100],
            showTotal: () =>
              pageSnapshot
                ? `第 ${pageSnapshot.page} 页 · 当前 ${pageSnapshot.itemCount} 条`
                : '',
            onChange: (nextPage, nextPageSize) => {
              if (nextPageSize !== pageSize) {
                cursorByPage.current = new Map([[1, undefined]]);
                setPageSize(nextPageSize);
                setCurrentPage(1);
                setPaginationTotal(0);
                return;
              }
              setCurrentPage(nextPage);
            },
          }}
        />
      </>
    );
  })();

  return (
    <PageContainer
      {...pageHeader(
        '投递订单',
        '设备事实保持只读；审核与纠正以追加认定版本和精确钱包差额生效。',
      )}
    >
      <DirectoryScopeBar scope={scope} />
      {content}

      <DeliveryOrderDetailDrawer
        open={drawerOpen}
        loading={detailLoading}
        order={detail}
        canReview={canReview}
        canCorrect={canCorrect}
        onClose={closeDetail}
        onReview={() => setMutationKind('review')}
        onCorrect={() => setMutationKind('correction')}
      />

      <DeliveryReviewModal
        kind={mutationKind ?? 'review'}
        order={detail}
        open={mutationKind !== null}
        submitting={submitting}
        onOpenChange={(open) => {
          if (!open && !submitting) setMutationKind(null);
        }}
        onSubmit={submitReview}
      />
    </PageContainer>
  );
}
