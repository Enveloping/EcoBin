import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { EditOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons';
import { PageContainer, ProTable, type ProColumns } from '@ant-design/pro-components';
import {
  Alert,
  App,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Image,
  Input,
  InputNumber,
  List,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import type { Dayjs } from 'dayjs';
import { Link, useSearchParams } from 'react-router-dom';
import {
  editCleanRecord,
  getCleanRecord,
  listCleanRecordChanges,
  listCleanRecords,
  type CleanRecordChange,
  type CleanRecordDetail,
  type CleanRecordItem,
  type CleanRecordListParams,
  type EditCleanRecordRequest,
} from '@/api/cleanRecords';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { useAuthStore } from '@/stores/authStore';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';

const { RangePicker } = DatePicker;

interface SearchValues {
  resultKind?: string;
  cleanerUserUid?: string;
  deviceCode?: string;
  portNo?: number;
  removedBagQr?: string;
  installedBagQr?: string;
  anomalyCode?: string;
  photoCompleteness?: string;
  occurredRange?: [Dayjs, Dayjs];
}

interface EditValues {
  weightAction: 'NONE' | 'SET' | 'CLEAR';
  weightValueKg?: string;
  remarkAction: 'NONE' | 'SET' | 'CLEAR';
  remarkValue?: string;
  reason: string;
}

const CLEAN_RECORD_NO = /^[A-Z0-9][A-Z0-9-]{0,63}$/i;
const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function errorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '清运记录加载失败';
}

function weight(value: string | null | undefined): string {
  return value === null || value === undefined ? '不可用' : `${value} kg`;
}

function optionalTime(value: string | null): string {
  return value ? formatShanghaiTime(value) : '无';
}

function recordKind(kind: string) {
  return kind === 'NORMAL'
    ? <Tag color="success">正常</Tag>
    : <Tag color="warning">系统异常</Tag>;
}

function CleanRecordDrawer({
  open,
  loading,
  detail,
  changes,
  changesLoading,
  moreChanges,
  operationUrl,
  canEdit,
  onLoadMoreChanges,
  onEdit,
  onClose,
}: {
  open: boolean;
  loading: boolean;
  detail: CleanRecordDetail | null;
  changes: CleanRecordChange[];
  changesLoading: boolean;
  moreChanges: boolean;
  operationUrl?: string;
  canEdit: boolean;
  onLoadMoreChanges: () => void;
  onEdit: () => void;
  onClose: () => void;
}) {
  return (
    <Drawer
      width={860}
      open={open}
      title={detail ? `清运记录 · ${detail.cleanRecordNo}` : '清运记录详情'}
      onClose={onClose}
      destroyOnClose
      extra={detail && canEdit ? (
        <Button icon={<EditOutlined />} onClick={onEdit}>修正记录</Button>
      ) : null}
    >
      {loading ? <Spin /> : detail ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            showIcon
            type={detail.resultKind === 'NORMAL' ? 'success' : 'warning'}
            message={detail.resultKind === 'NORMAL' ? '清运已正常完成' : '清运已完成，但存在系统异常'}
            description="记录是设备完成事件形成的业务事实；后台修正只改变有效重量或备注，不会改写设备原始数据。"
          />
          <Card size="small" title="来源">
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="记录号">{detail.cleanRecordNo}</Descriptions.Item>
              <Descriptions.Item label="结果">{recordKind(detail.resultKind)}</Descriptions.Item>
              <Descriptions.Item label="清运操作">
                {operationUrl ? <Link to={operationUrl}>{detail.source.operationUid}</Link> : detail.source.operationUid}
              </Descriptions.Item>
              <Descriptions.Item label="版本">v{detail.effective.version}</Descriptions.Item>
              <Descriptions.Item label="设备">{detail.source.deviceCode}</Descriptions.Item>
              <Descriptions.Item label="投口">{detail.source.portNo}</Descriptions.Item>
              <Descriptions.Item label="清运员 UID" span={2}>
                <Typography.Text copyable>{detail.source.cleanerUserUid}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="设备完成时间">{formatShanghaiTime(detail.source.deviceCompletedAt)}</Descriptions.Item>
              <Descriptions.Item label="后端接收时间">{formatShanghaiTime(detail.source.backendReceivedAt)}</Descriptions.Item>
              <Descriptions.Item label="事件 UID"><Typography.Text copyable>{detail.source.eventUid}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="命令 UID"><Typography.Text copyable>{detail.source.commandUid}</Typography.Text></Descriptions.Item>
            </Descriptions>
          </Card>
          <Card size="small" title="袋码与重量">
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="取下袋码">{detail.bags.removedBagQr ?? '原先无袋'}</Descriptions.Item>
              <Descriptions.Item label="装入袋码">{detail.bags.installedBagQr}</Descriptions.Item>
              <Descriptions.Item label="解锁前重量">{weight(detail.weights.preUnlockWeightKg)}</Descriptions.Item>
              <Descriptions.Item label="旧基线重量">{weight(detail.weights.oldBaselineWeightKg)}</Descriptions.Item>
              <Descriptions.Item label="设备计算净重">{weight(detail.weights.deviceRemovedNetWeightKg)}</Descriptions.Item>
              <Descriptions.Item label="后端重算净重">{weight(detail.weights.recalculatedRemovedNetWeightKg)}</Descriptions.Item>
              <Descriptions.Item label="设备最终总重">{weight(detail.weights.finalTotalWeightKg)}</Descriptions.Item>
              <Descriptions.Item label="新袋候选基线">{weight(detail.weights.candidateNewBaselineWeightKg)}</Descriptions.Item>
              <Descriptions.Item label="当前有效净重">
                <Typography.Text strong>{weight(detail.effective.removedNetWeightKg)}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="有效值来源">{detail.effective.source}</Descriptions.Item>
              <Descriptions.Item label="计入已知重量统计">
                {detail.effective.includedInKnownWeightStatistics ? '是' : '否'}
              </Descriptions.Item>
              <Descriptions.Item label="记录备注">{detail.effective.recordRemark ?? '无'}</Descriptions.Item>
            </Descriptions>
          </Card>
          <Card size="small" title="新袋基线与关门后检测">
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="新基线建立">{detail.newBaseline.established ? '已建立' : '未建立'}</Descriptions.Item>
              <Descriptions.Item label="基线重量">{weight(detail.newBaseline.baselineWeightKg)}</Descriptions.Item>
              <Descriptions.Item label="检测状态">{detail.postCleanDetection.status ?? '未产生'}</Descriptions.Item>
              <Descriptions.Item label="检测结果">{detail.postCleanDetection.finalResult ?? '无'}</Descriptions.Item>
              <Descriptions.Item label="检测失败码">{detail.postCleanDetection.failureCode ?? '无'}</Descriptions.Item>
              <Descriptions.Item label="检测完成时间">{optionalTime(detail.postCleanDetection.completedAt)}</Descriptions.Item>
            </Descriptions>
          </Card>
          <Card size="small" title={`异常（${detail.anomalies.length}）`}>
            {detail.anomalies.length ? (
              <List
                dataSource={detail.anomalies}
                renderItem={(anomaly) => (
                  <List.Item>
                    <List.Item.Meta
                      title={<Tag color="warning">{anomaly.code}</Tag>}
                      description={(
                        <Space direction="vertical">
                          <span>{formatShanghaiTime(anomaly.detectedAt)}</span>
                          <Typography.Text code>{JSON.stringify(anomaly.diagnosticDetails)}</Typography.Text>
                        </Space>
                      )}
                    />
                  </List.Item>
                )}
              />
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无异常" />}
          </Card>
          <Card size="small" title="设备照片">
            <Space wrap align="start">
              {detail.photos.map((photo) => (
                <Card key={photo.position} size="small" style={{ width: 185 }}>
                  <Typography.Text strong>{photo.position}</Typography.Text>
                  <div style={{ marginTop: 8 }}>
                    {photo.status === 'AVAILABLE' && photo.url
                      ? <Image width={150} height={100} style={{ objectFit: 'cover' }} src={photo.url} />
                      : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={photo.missingReason ?? photo.status} />}
                  </div>
                </Card>
              ))}
            </Space>
          </Card>
          <Card size="small" title="后台修正历史">
            <List
              loading={changesLoading}
              dataSource={changes}
              locale={{ emptyText: '尚无后台修正' }}
              renderItem={(change) => (
                <List.Item>
                  <List.Item.Meta
                    title={`v${change.fromVersion} → v${change.toVersion} · ${change.actor.displayName}`}
                    description={(
                      <Space direction="vertical" size={2}>
                        <span>{formatShanghaiTime(change.changedAt)} · 原因：{change.reason}</span>
                        <span>重量：{weight(change.beforeEffectiveRemovedNetWeightKg)} → {weight(change.afterEffectiveRemovedNetWeightKg)}</span>
                        <span>备注：{change.beforeRecordRemark ?? '无'} → {change.afterRecordRemark ?? '无'}</span>
                      </Space>
                    )}
                  />
                </List.Item>
              )}
              footer={moreChanges ? <Button onClick={onLoadMoreChanges}>加载更多历史</Button> : null}
            />
          </Card>
        </Space>
      ) : <Empty description="未加载到清运记录" />}
    </Drawer>
  );
}

export default function CleanRecordsPage() {
  const scope = useDirectoryScope();
  const organizationScope = useOrganizationScope(scope);
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedCleanerUserUid =
    searchParams.get('cleanerUserUid')?.trim() || undefined;
  const linkedCleanerUserUid = requestedCleanerUserUid
    && UUID_V4.test(requestedCleanerUserUid)
    ? requestedCleanerUserUid
    : undefined;
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const canEdit = useAuthStore((state) => state.hasCapability('clean.edit'));
  const [searchForm] = Form.useForm<SearchValues>();
  const [editForm] = Form.useForm<EditValues>();
  const cursorByPage = useRef<Map<number, string | undefined>>(new Map([[1, undefined]]));
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const selectedRecordNo = useRef<string | null>(null);
  const [filters, setFilters] = useState<CleanRecordListParams>(() => (
    linkedCleanerUserUid
      ? { cleanerUserUid: linkedCleanerUserUid }
      : {}
  ));
  const [items, setItems] = useState<CleanRecordItem[]>([]);
  const [page, setPage] = useState(1);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [asOf, setAsOf] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<CleanRecordDetail | null>(null);
  const [changes, setChanges] = useState<CleanRecordChange[]>([]);
  const [changesCursor, setChangesCursor] = useState<string | null>(null);
  const [changesLoading, setChangesLoading] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const organizationCode = organizationScope.organizationCode;

  const loadPage = useCallback(async (targetPage: number, nextFilters = filters) => {
    if (!scope.context || !organizationCode) return;
    const sequence = ++listSequence.current;
    setLoading(true);
    setError(undefined);
    try {
      const result = await listCleanRecords(scope.context, organizationCode, {
        ...nextFilters,
        cursor: cursorByPage.current.get(targetPage),
        limit: 20,
      });
      if (sequence !== listSequence.current) return;
      setItems(result.items);
      setPage(targetPage);
      setNextCursor(result.nextCursor);
      setAsOf(result.asOf);
      if (result.nextCursor) cursorByPage.current.set(targetPage + 1, result.nextCursor);
      else cursorByPage.current.delete(targetPage + 1);
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
    selectedRecordNo.current = null;
    setDrawerOpen(false);
    setDetail(null);
    if (scope.context && organizationCode) void loadPage(1, filters);
  }, [scope.context, organizationCode, filters]);

  const loadChanges = useCallback(async (recordNo: string, cursor?: string) => {
    if (!scope.context || !organizationCode) return;
    setChangesLoading(true);
    try {
      const result = await listCleanRecordChanges(
        scope.context,
        organizationCode,
        recordNo,
        { cursor, limit: 20 },
      );
      if (selectedRecordNo.current !== recordNo) return;
      setChanges((current) => cursor ? [...current, ...result.items] : result.items);
      setChangesCursor(result.nextCursor);
    } catch (caught) {
      if (selectedRecordNo.current === recordNo) {
        message.error(`修正历史加载失败：${errorMessage(caught)}`);
      }
    } finally {
      if (selectedRecordNo.current === recordNo) {
        setChangesLoading(false);
      }
    }
  }, [message, organizationCode, scope.context]);

  const loadDetail = useCallback(async (recordNo: string) => {
    if (!scope.context || !organizationCode) return;
    const sequence = ++detailSequence.current;
    setDetailLoading(true);
    try {
      const loaded = await getCleanRecord(scope.context, organizationCode, recordNo);
      if (
        sequence === detailSequence.current
        && selectedRecordNo.current === recordNo
      ) setDetail(loaded);
      return loaded;
    } finally {
      if (sequence === detailSequence.current) setDetailLoading(false);
    }
  }, [organizationCode, scope.context]);

  const openDetail = useCallback(async (recordNo: string) => {
    if (!CLEAN_RECORD_NO.test(recordNo)) return;
    selectedRecordNo.current = recordNo;
    setError(undefined);
    setDrawerOpen(true);
    setDetail(null);
    setChanges([]);
    setChangesCursor(null);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set('cleanRecordNo', recordNo);
      return next;
    }, { replace: true });
    try {
      await Promise.all([loadDetail(recordNo), loadChanges(recordNo)]);
    } catch (caught) {
      if (selectedRecordNo.current === recordNo) {
        setError(errorMessage(caught));
      }
    }
  }, [loadChanges, loadDetail, setSearchParams]);

  const linkedRecordNo = searchParams.get('cleanRecordNo')?.trim();
  useEffect(() => {
    if (linkedRecordNo && CLEAN_RECORD_NO.test(linkedRecordNo) && !drawerOpen && scope.context && organizationCode) {
      void openDetail(linkedRecordNo);
    }
  }, [drawerOpen, linkedRecordNo, openDetail, organizationCode, scope.context]);

  const closeDrawer = () => {
    detailSequence.current += 1;
    selectedRecordNo.current = null;
    setDrawerOpen(false);
    setDetail(null);
    setChanges([]);
    setChangesCursor(null);
    setEditOpen(false);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete('cleanRecordNo');
      return next;
    }, { replace: true });
  };

  const openEdit = () => {
    editForm.setFieldsValue({
      weightAction: 'NONE',
      remarkAction: 'NONE',
      reason: '',
    });
    setEditOpen(true);
  };

  const submitEdit = async (values: EditValues) => {
    if (!detail || !scope.context || !organizationCode) return;
    if (values.weightAction === 'NONE' && values.remarkAction === 'NONE') {
      message.warning('请至少选择一项需要修正的内容');
      return;
    }
    const request: EditCleanRecordRequest = {
      expectedVersion: detail.effective.version,
      reason: values.reason.trim(),
    };
    if (values.weightAction !== 'NONE') {
      request.effectiveRemovedNetWeight = {
        action: values.weightAction,
        ...(values.weightAction === 'SET' ? { valueKg: values.weightValueKg?.trim() } : {}),
      };
    }
    if (values.remarkAction !== 'NONE') {
      request.recordRemark = {
        action: values.remarkAction,
        ...(values.remarkAction === 'SET' ? { value: values.remarkValue?.trim() } : {}),
      };
    }
    const recordNo = detail.cleanRecordNo;
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey('edit-clean-record', recordNo, request),
        (intent) => editCleanRecord(scope.context!, organizationCode, recordNo, request, intent),
      );
      message.success('清运记录已修正，设备原始事实保持不变');
      setEditOpen(false);
      setChanges([]);
      setChangesCursor(null);
      await Promise.all([loadDetail(recordNo), loadChanges(recordNo)]);
      void loadPage(page);
    } catch (caught) {
      if (caught instanceof ApiProblem && caught.status === 409) {
        setEditOpen(false);
        try {
          await loadDetail(recordNo);
          message.warning('记录版本已经变化，已载入最新内容，请重新核对后修正');
        } catch (reloadError) {
          message.error(`记录刷新失败：${errorMessage(reloadError)}`);
        }
      } else {
        message.error(`清运记录修正失败：${errorMessage(caught)}`);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const columns = useMemo<ProColumns<CleanRecordItem>[]>(() => [
    {
      title: '记录号', dataIndex: 'cleanRecordNo', width: 185,
      render: (_, row) => <Button type="link" style={{ padding: 0 }} onClick={() => void openDetail(row.cleanRecordNo)}>{row.cleanRecordNo}</Button>,
    },
    { title: '结果', dataIndex: 'resultKind', width: 110, render: (_, row) => recordKind(row.resultKind) },
    {
      title: '设备 / 投口', key: 'device', width: 170,
      render: (_, row) => <Space direction="vertical" size={0}><Typography.Text code>{row.deviceCode}</Typography.Text><Typography.Text type="secondary">投口 {row.portNo}</Typography.Text></Space>,
    },
    { title: '清运员 UID', dataIndex: 'cleanerUserUid', width: 210, render: (_, row) => <Typography.Text copyable ellipsis>{row.cleanerUserUid}</Typography.Text> },
    {
      title: '袋码', key: 'bags', width: 200,
      render: (_, row) => <Space direction="vertical" size={0}><span>取下：{row.removedBagQr ?? '无'}</span><span>装入：{row.installedBagQr}</span></Space>,
    },
    {
      title: '有效净重', dataIndex: 'effectiveRemovedNetWeightKg', width: 140,
      render: (_, row) => <Space direction="vertical" size={0}><Typography.Text strong>{weight(row.effectiveRemovedNetWeightKg)}</Typography.Text><Typography.Text type="secondary">{row.effectiveWeightSource}</Typography.Text></Space>,
    },
    {
      title: '证据', key: 'evidence', width: 130,
      render: (_, row) => <Space direction="vertical" size={2}><Tag color={row.photoCompleteness === 'COMPLETE' ? 'success' : 'warning'}>{row.photoCompleteness === 'COMPLETE' ? '照片完整' : '照片不完整'}</Tag>{row.anomalyCodes.length ? <Tag color="warning">{row.anomalyCodes.length} 项异常</Tag> : <span>无异常</span>}</Space>,
    },
    { title: '设备完成时间', dataIndex: 'deviceCompletedAt', width: 170, render: (_, row) => formatShanghaiTime(row.deviceCompletedAt) },
    { title: '操作', key: 'actions', width: 90, fixed: 'right', render: (_, row) => <Button icon={<EyeOutlined />} onClick={() => void openDetail(row.cleanRecordNo)}>详情</Button> },
  ], [openDetail]);

  const operationUrl = detail ? (() => {
    const params = new URLSearchParams();
    if (scope.tenantCode) params.set('tenant', scope.tenantCode);
    if (organizationCode) params.set('organization', organizationCode);
    params.set('operationUid', detail.source.operationUid);
    return `/clean-operations?${params.toString()}`;
  })() : undefined;
  const weightAction = Form.useWatch('weightAction', editForm);
  const remarkAction = Form.useWatch('remarkAction', editForm);

  return (
    <PageContainer
      {...pageHeader(
        '清运记录',
        '查询设备完成后形成的清运事实；具备清运编辑能力的账号可追加重量或备注修正历史。',
      )}
    >
      <DirectoryScopeBar scope={scope} />
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Space wrap>
          <Typography.Text strong>目标机构</Typography.Text>
          <Select showSearch optionFilterProp="label" style={{ width: 340 }} loading={organizationScope.loading} value={organizationCode} options={organizationScope.organizationOptions} onChange={organizationScope.setOrganizationCode} placeholder="选择机构" />
        </Space>
        <Form<SearchValues>
          form={searchForm}
          initialValues={{ cleanerUserUid: linkedCleanerUserUid }}
          layout="inline"
          onFinish={(values) => {
            const next: CleanRecordListParams = {
              resultKind: values.resultKind,
              cleanerUserUid: values.cleanerUserUid?.trim() || undefined,
              deviceCode: values.deviceCode?.trim() || undefined,
              portNo: values.portNo,
              removedBagQr: values.removedBagQr?.trim() || undefined,
              installedBagQr: values.installedBagQr?.trim() || undefined,
              anomalyCode: values.anomalyCode?.trim() || undefined,
              photoCompleteness: values.photoCompleteness,
              occurredFrom: values.occurredRange?.[0].toISOString(),
              occurredTo: values.occurredRange?.[1].toISOString(),
            };
            cursorByPage.current = new Map([[1, undefined]]);
            setFilters(next);
          }}
        >
          <Form.Item name="resultKind" label="结果"><Select allowClear style={{ width: 130 }} options={[{ value: 'NORMAL', label: '正常' }, { value: 'SYSTEM_ANOMALY', label: '系统异常' }]} /></Form.Item>
          <Form.Item name="cleanerUserUid" label="清运员"><Input allowClear style={{ width: 220 }} placeholder="完整用户 UID" /></Form.Item>
          <Form.Item name="deviceCode" label="设备"><Input allowClear style={{ width: 150 }} /></Form.Item>
          <Form.Item name="portNo" label="投口"><InputNumber min={1} max={6} style={{ width: 80 }} /></Form.Item>
          <Form.Item name="removedBagQr" label="取下袋码"><Input allowClear style={{ width: 160 }} /></Form.Item>
          <Form.Item name="installedBagQr" label="装入袋码"><Input allowClear style={{ width: 160 }} /></Form.Item>
          <Form.Item name="anomalyCode" label="异常码"><Input allowClear style={{ width: 140 }} /></Form.Item>
          <Form.Item name="photoCompleteness" label="照片"><Select allowClear style={{ width: 130 }} options={[{ value: 'COMPLETE', label: '完整' }, { value: 'INCOMPLETE', label: '不完整' }]} /></Form.Item>
          <Form.Item name="occurredRange" label="完成时间"><RangePicker showTime /></Form.Item>
          <Form.Item><Button type="primary" htmlType="submit">查询</Button></Form.Item>
          <Form.Item><Button onClick={() => {
            searchForm.resetFields();
            searchForm.setFieldValue('cleanerUserUid', undefined);
            setSearchParams((current) => {
              const next = new URLSearchParams(current);
              next.delete('cleanerUserUid');
              return next;
            }, { replace: true });
            setFilters({});
          }}>重置</Button></Form.Item>
        </Form>
        {error && <Alert showIcon type="error" message="清运记录加载失败" description={error} />}
        {!scope.context || !organizationCode ? <Empty description="请先选择租户和机构" /> : (
          <ProTable<CleanRecordItem>
            {...proTableConfig}
            rowKey="cleanRecordNo"
            search={false}
            loading={loading}
            columns={columns}
            dataSource={items}
            pagination={false}
            columnsState={{
              persistenceKey: 'ecobin.web.columns.clean-records.v1',
              persistenceType: 'localStorage',
            }}
            options={{
              density: false,
              fullScreen: false,
              reload: false,
              setting: true,
            }}
            toolBarRender={() => [<Button key="reload" icon={<ReloadOutlined />} onClick={() => void loadPage(page)}>刷新</Button>]}
          />
        )}
        <Space style={{ justifyContent: 'space-between', width: '100%' }}>
          <Typography.Text type="secondary">第 {page} 页{asOf ? ` · 数据时间 ${formatShanghaiTime(asOf)}` : ''}</Typography.Text>
          <Space><Button disabled={page <= 1 || loading} onClick={() => void loadPage(page - 1)}>上一页</Button><Button disabled={!nextCursor || loading} onClick={() => void loadPage(page + 1)}>下一页</Button></Space>
        </Space>
      </Space>
      <CleanRecordDrawer
        open={drawerOpen}
        loading={detailLoading}
        detail={detail}
        changes={changes}
        changesLoading={changesLoading}
        moreChanges={!!changesCursor}
        operationUrl={operationUrl}
        canEdit={canEdit}
        onLoadMoreChanges={() => { if (detail && changesCursor) void loadChanges(detail.cleanRecordNo, changesCursor); }}
        onEdit={openEdit}
        onClose={closeDrawer}
      />
      <Modal
        open={editOpen}
        title="修正清运记录"
        okText="提交修正"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={() => setEditOpen(false)}
        onOk={() => void editForm.submit()}
        destroyOnClose
      >
        <Alert style={{ marginBottom: 16 }} showIcon type="info" message="修正会追加一条不可覆盖的历史记录，不会改写设备原始重量。" />
        <Form<EditValues> form={editForm} layout="vertical" onFinish={(values) => void submitEdit(values)}>
          <Form.Item name="weightAction" label="有效净重" initialValue="NONE" rules={[{ required: true }]}>
            <Select options={[{ value: 'NONE', label: '不修改' }, { value: 'SET', label: '设置人工净重' }, { value: 'CLEAR', label: '清除人工净重' }]} />
          </Form.Item>
          {weightAction === 'SET' && <Form.Item name="weightValueKg" label="净重（kg）" rules={[{ required: true }, { pattern: /^(?:0|[1-9]\d{0,2}|1000)(?:\.\d{1,2})?$/, message: '请输入 0.00 到 1000.00，最多两位小数' }]}><Input /></Form.Item>}
          <Form.Item name="remarkAction" label="记录备注" initialValue="NONE" rules={[{ required: true }]}>
            <Select options={[{ value: 'NONE', label: '不修改' }, { value: 'SET', label: '设置备注' }, { value: 'CLEAR', label: '清除备注' }]} />
          </Form.Item>
          {remarkAction === 'SET' && <Form.Item name="remarkValue" label="新备注" rules={[{ required: true, whitespace: true }, { max: 500 }]}><Input.TextArea rows={3} /></Form.Item>}
          <Divider />
          <Form.Item name="reason" label="修正原因" rules={[{ required: true, whitespace: true }, { max: 500 }]}><Input.TextArea rows={3} placeholder="说明为什么需要修正，便于以后追溯" /></Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
