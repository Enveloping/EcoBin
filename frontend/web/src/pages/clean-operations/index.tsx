import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { EyeOutlined, ReloadOutlined } from '@ant-design/icons';
import { PageContainer, ProTable, type ProColumns } from '@ant-design/pro-components';
import {
  Alert,
  Button,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import type { Dayjs } from 'dayjs';
import { Link, useSearchParams } from 'react-router-dom';
import {
  getCleanOperation,
  listCleanOperations,
  type CleanOperationDetail,
  type CleanOperationItem,
  type CleanOperationListParams,
  type CleanOperationStatus,
} from '@/api/cleanOperations';
import { ApiProblem } from '@/api/request';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';

const { RangePicker } = DatePicker;

interface SearchValues {
  status?: CleanOperationStatus;
  cleanerUserUid?: string;
  deviceCode?: string;
  portNo?: number;
  createdRange?: [Dayjs, Dayjs];
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const statusMeta: Record<
  CleanOperationStatus,
  { label: string; color: string; description: string }
> = {
  PREPARED: {
    label: '已准备',
    color: 'default',
    description: '后端已创建操作，尚未确认香橙派持久化。',
  },
  EDGE_SAVED: {
    label: '边缘端已保存',
    color: 'processing',
    description: '香橙派已把启动命令保存到本地，但还不能证明投口已解锁。',
  },
  IN_PROGRESS: {
    label: '进行中',
    color: 'blue',
    description: '启动命令已写向 MCU，第一次解锁可能已经发生。',
  },
  RECOVERY_REQUIRED: {
    label: '需要恢复',
    color: 'warning',
    description: '设备结果不确定，系统保留投口和袋码占用，等待现场恢复。',
  },
  PRE_UNLOCK_ENDED: {
    label: '解锁前结束',
    color: 'default',
    description: '系统确认没有发生第一次解锁，操作已结束并释放占用。',
  },
  COMPLETED: {
    label: '已完成',
    color: 'success',
    description: '设备已提交完成事实，并形成清运记录。',
  },
  ABORTED: {
    label: '已中止',
    color: 'error',
    description: '香橙派在固定帧执行期间重启，系统中止操作并进入安全联锁。',
  },
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '清运操作加载失败';
}

function optionalTime(value: string | null): string {
  return value ? formatShanghaiTime(value) : '无';
}

function fact(value: boolean, yes: string, no: string) {
  return <Tag color={value ? 'success' : 'default'}>{value ? yes : no}</Tag>;
}

function OperationDrawer({
  open,
  loading,
  detail,
  recordUrl,
  onClose,
}: {
  open: boolean;
  loading: boolean;
  detail: CleanOperationDetail | null;
  recordUrl?: string;
  onClose: () => void;
}) {
  return (
    <Drawer
      width={760}
      open={open}
      title="清运操作详情"
      onClose={onClose}
      destroyOnClose
    >
      {loading ? <Spin /> : detail ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            showIcon
            type={detail.status === 'RECOVERY_REQUIRED' ? 'warning' : 'info'}
            message={statusMeta[detail.status].label}
            description={statusMeta[detail.status].description}
          />
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="操作 UID" span={2}>
              <Typography.Text copyable>{detail.operationUid}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="设备">{detail.deviceCode}</Descriptions.Item>
            <Descriptions.Item label="投口">{detail.portNo}</Descriptions.Item>
            <Descriptions.Item label="清运员 UID" span={2}>
              <Typography.Text copyable>{detail.cleanerUserUid}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="旧袋状态">
              {detail.oldBagBindingState === 'BOUND' ? '已绑定' : '原先无袋'}
            </Descriptions.Item>
            <Descriptions.Item label="取下袋码">
              {detail.removedBagQr ?? '无'}
            </Descriptions.Item>
            <Descriptions.Item label="装入袋码" span={2}>
              <Typography.Text copyable>{detail.installedBagQr}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="解锁前称重">
              {detail.preUnlockWeightStatus}
              {detail.preUnlockWeightKg ? ` · ${detail.preUnlockWeightKg} kg` : ''}
            </Descriptions.Item>
            <Descriptions.Item label="称重故障码">
              {detail.preUnlockWeightFaultCode ?? '无'}
            </Descriptions.Item>
            <Descriptions.Item label="边缘端已保存">
              {fact(detail.edgeSavedConfirmed, '已确认', '未确认')}
            </Descriptions.Item>
            <Descriptions.Item label="第一次解锁可能发生">
              {fact(detail.firstUnlockMayHaveExecuted, '可能发生', '未发生')}
            </Descriptions.Item>
            <Descriptions.Item label="锁已断电">
              {fact(detail.cleanLockDeenergizedConfirmed, '已确认', '未确认')}
            </Descriptions.Item>
            <Descriptions.Item label="清运员已关门">
              {fact(detail.cleanerPhysicalCloseConfirmed, '已确认', '未确认')}
            </Descriptions.Item>
            <Descriptions.Item label="边缘保存时间">
              {optionalTime(detail.edgeSavedAt)}
            </Descriptions.Item>
            <Descriptions.Item label="首次可能解锁时间">
              {optionalTime(detail.firstPossibleUnlockAt)}
            </Descriptions.Item>
            <Descriptions.Item label="电磁锁断电时间">
              {optionalTime(detail.solenoidPoweredOffAt)}
            </Descriptions.Item>
            <Descriptions.Item label="人工确认关门时间">
              {optionalTime(detail.cleanerConfirmedClosedAt)}
            </Descriptions.Item>
            <Descriptions.Item label="执行截止时间">
              {optionalTime(detail.executionDeadlineAt)}
            </Descriptions.Item>
            <Descriptions.Item label="版本">v{detail.version}</Descriptions.Item>
            <Descriptions.Item label="重开次数">{detail.reopenCount}</Descriptions.Item>
            <Descriptions.Item label="恢复次数">{detail.recoveryCount}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatShanghaiTime(detail.createdAt)}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{formatShanghaiTime(detail.updatedAt)}</Descriptions.Item>
            <Descriptions.Item label="结束时间">{optionalTime(detail.endedAt)}</Descriptions.Item>
            <Descriptions.Item label="结束原因">{detail.endReason ?? '无'}</Descriptions.Item>
            <Descriptions.Item label="清运记录" span={2}>
              {detail.cleanRecordNo && recordUrl ? (
                <Link to={recordUrl}>{detail.cleanRecordNo}</Link>
              ) : '尚未形成'}
            </Descriptions.Item>
          </Descriptions>
        </Space>
      ) : <Empty description="未加载到操作详情" />}
    </Drawer>
  );
}

export default function CleanOperationsPage() {
  const scope = useDirectoryScope();
  const organizationScope = useOrganizationScope(scope);
  const [searchParams, setSearchParams] = useSearchParams();
  const [form] = Form.useForm<SearchValues>();
  const cursorByPage = useRef<Map<number, string | undefined>>(
    new Map([[1, undefined]]),
  );
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const [filters, setFilters] = useState<CleanOperationListParams>({});
  const [items, setItems] = useState<CleanOperationItem[]>([]);
  const [page, setPage] = useState(1);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [asOf, setAsOf] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<CleanOperationDetail | null>(null);
  const organizationCode = organizationScope.organizationCode;

  const loadPage = useCallback(async (
    targetPage: number,
    nextFilters = filters,
  ) => {
    if (!scope.context || !organizationCode) return;
    const sequence = ++listSequence.current;
    setLoading(true);
    setError(undefined);
    try {
      const result = await listCleanOperations(
        scope.context,
        organizationCode,
        {
          ...nextFilters,
          cursor: cursorByPage.current.get(targetPage),
          limit: 20,
        },
      );
      if (sequence !== listSequence.current) return;
      setItems(result.items);
      setPage(targetPage);
      setNextCursor(result.nextCursor);
      setAsOf(result.asOf);
      if (result.nextCursor) {
        cursorByPage.current.set(targetPage + 1, result.nextCursor);
      } else {
        cursorByPage.current.delete(targetPage + 1);
      }
    } catch (caught) {
      if (sequence !== listSequence.current) return;
      setError(errorMessage(caught));
      setItems([]);
    } finally {
      if (sequence === listSequence.current) setLoading(false);
    }
  }, [filters, organizationCode, scope.context]);

  useEffect(() => {
    listSequence.current += 1;
    detailSequence.current += 1;
    cursorByPage.current = new Map([[1, undefined]]);
    setPage(1);
    setItems([]);
    setDrawerOpen(false);
    setDetail(null);
    if (scope.context && organizationCode) void loadPage(1, filters);
  // loadPage intentionally changes with the selected scope and filters.
  }, [scope.context, organizationCode, filters]);

  const openDetail = useCallback(async (operationUid: string) => {
    if (!scope.context || !organizationCode || !UUID_V4.test(operationUid)) return;
    const sequence = ++detailSequence.current;
    setError(undefined);
    setDrawerOpen(true);
    setDetail(null);
    setDetailLoading(true);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set('operationUid', operationUid);
      return next;
    }, { replace: true });
    try {
      const loaded = await getCleanOperation(
        scope.context,
        organizationCode,
        operationUid,
      );
      if (detailSequence.current === sequence) setDetail(loaded);
    } catch (caught) {
      if (detailSequence.current === sequence) setError(errorMessage(caught));
    } finally {
      if (detailSequence.current === sequence) setDetailLoading(false);
    }
  }, [organizationCode, scope.context, setSearchParams]);

  const linkedOperationUid = searchParams.get('operationUid')?.trim();
  useEffect(() => {
    if (
      linkedOperationUid
      && UUID_V4.test(linkedOperationUid)
      && !drawerOpen
      && scope.context
      && organizationCode
    ) {
      void openDetail(linkedOperationUid);
    }
  }, [drawerOpen, linkedOperationUid, openDetail, organizationCode, scope.context]);

  const closeDrawer = () => {
    detailSequence.current += 1;
    setDrawerOpen(false);
    setDetail(null);
    setDetailLoading(false);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete('operationUid');
      return next;
    }, { replace: true });
  };

  const columns = useMemo<ProColumns<CleanOperationItem>[]>(() => [
    {
      title: '操作 UID',
      dataIndex: 'operationUid',
      width: 210,
      render: (_, row) => (
        <Button type="link" style={{ padding: 0 }} onClick={() => void openDetail(row.operationUid)}>
          {row.operationUid}
        </Button>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 130,
      render: (_, row) => <Tag color={statusMeta[row.status].color}>{statusMeta[row.status].label}</Tag>,
    },
    {
      title: '设备 / 投口',
      key: 'device',
      width: 180,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text code>{row.deviceCode}</Typography.Text>
          <Typography.Text type="secondary">投口 {row.portNo}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '清运员 UID',
      dataIndex: 'cleanerUserUid',
      width: 210,
      render: (_, row) => <Typography.Text copyable ellipsis>{row.cleanerUserUid}</Typography.Text>,
    },
    {
      title: '袋码',
      key: 'bags',
      width: 200,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary">取下：{row.removedBagQr ?? '无'}</Typography.Text>
          <Typography.Text>装入：{row.installedBagQr}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '关键事实',
      key: 'facts',
      width: 180,
      render: (_, row) => (
        <Space wrap size={4}>
          {fact(row.edgeSavedConfirmed, '边缘已保存', '边缘未保存')}
          {fact(row.firstUnlockMayHaveExecuted, '可能已解锁', '未解锁')}
        </Space>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (_, row) => formatShanghaiTime(row.createdAt),
    },
    {
      title: '操作',
      key: 'actions',
      width: 90,
      fixed: 'right',
      render: (_, row) => (
        <Button icon={<EyeOutlined />} onClick={() => void openDetail(row.operationUid)}>详情</Button>
      ),
    },
  ], [openDetail]);

  const recordUrl = detail?.cleanRecordNo ? (() => {
    const params = new URLSearchParams();
    if (scope.tenantCode) params.set('tenant', scope.tenantCode);
    if (organizationCode) params.set('organization', organizationCode);
    params.set('cleanRecordNo', detail.cleanRecordNo);
    return `/clean-records?${params.toString()}`;
  })() : undefined;

  return (
    <PageContainer
      {...pageHeader(
        '清运操作',
        '查看从后端创建操作、香橙派保存命令、可能解锁到完成或安全结束的全过程。',
      )}
    >
      <DirectoryScopeBar scope={scope} />
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Space wrap>
          <Typography.Text strong>目标机构</Typography.Text>
          <Select
            showSearch
            optionFilterProp="label"
            style={{ width: 340 }}
            loading={organizationScope.loading}
            value={organizationCode}
            options={organizationScope.organizationOptions}
            onChange={organizationScope.setOrganizationCode}
            placeholder="选择机构"
          />
        </Space>
        <Form<SearchValues>
          form={form}
          layout="inline"
          onFinish={(values) => {
            const next: CleanOperationListParams = {
              status: values.status,
              cleanerUserUid: values.cleanerUserUid?.trim() || undefined,
              deviceCode: values.deviceCode?.trim() || undefined,
              portNo: values.portNo,
              createdFrom: values.createdRange?.[0].toISOString(),
              createdTo: values.createdRange?.[1].toISOString(),
            };
            cursorByPage.current = new Map([[1, undefined]]);
            setFilters(next);
          }}
        >
          <Form.Item name="status" label="状态"><Select allowClear style={{ width: 150 }} options={Object.entries(statusMeta).map(([value, meta]) => ({ value, label: meta.label }))} /></Form.Item>
          <Form.Item name="cleanerUserUid" label="清运员"><Input allowClear style={{ width: 230 }} placeholder="完整用户 UID" /></Form.Item>
          <Form.Item name="deviceCode" label="设备"><Input allowClear style={{ width: 160 }} /></Form.Item>
          <Form.Item name="portNo" label="投口"><InputNumber min={1} max={6} style={{ width: 90 }} /></Form.Item>
          <Form.Item name="createdRange" label="创建时间"><RangePicker showTime /></Form.Item>
          <Form.Item><Button type="primary" htmlType="submit">查询</Button></Form.Item>
          <Form.Item><Button onClick={() => { form.resetFields(); setFilters({}); }}>重置</Button></Form.Item>
        </Form>
        {error && <Alert showIcon type="error" message="清运操作加载失败" description={error} />}
        {!scope.context || !organizationCode ? (
          <Empty description="请先选择租户和机构" />
        ) : (
          <ProTable<CleanOperationItem>
            {...proTableConfig}
            rowKey="operationUid"
            search={false}
            loading={loading}
            columns={columns}
            dataSource={items}
            pagination={false}
            columnsState={{
              persistenceKey: 'ecobin.web.columns.clean-operations.v1',
              persistenceType: 'localStorage',
            }}
            options={{
              density: false,
              fullScreen: false,
              reload: false,
              setting: true,
            }}
            toolBarRender={() => [
              <Button key="reload" icon={<ReloadOutlined />} onClick={() => void loadPage(page)}>刷新</Button>,
            ]}
          />
        )}
        <Space style={{ justifyContent: 'space-between', width: '100%' }}>
          <Typography.Text type="secondary">第 {page} 页{asOf ? ` · 数据时间 ${formatShanghaiTime(asOf)}` : ''}</Typography.Text>
          <Space>
            <Button disabled={page <= 1 || loading} onClick={() => void loadPage(page - 1)}>上一页</Button>
            <Button disabled={!nextCursor || loading} onClick={() => void loadPage(page + 1)}>下一页</Button>
          </Space>
        </Space>
      </Space>
      <OperationDrawer
        open={drawerOpen}
        loading={detailLoading}
        detail={detail}
        recordUrl={recordUrl}
        onClose={closeDrawer}
      />
    </PageContainer>
  );
}
