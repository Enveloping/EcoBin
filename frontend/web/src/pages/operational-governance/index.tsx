import { useCallback, useEffect, useRef, useState } from 'react';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  EyeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import {
  getReliableTask,
  listReliableTaskAttempts,
  listReliableTasks,
  resumeReliableTask,
  type ReliableTask,
  type ReliableTaskAttempt,
  type ReliableTaskListParams,
  type ReliableTaskResumption,
  type ResumeReliableTaskRequest,
} from '@/api/operationsGovernance';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import './index.css';

interface ResumeFormValues {
  causeFixedConfirmed: boolean;
  reason: string;
}

interface RecoveryTracker {
  task: ReliableTask;
  pollAfterMs: number;
  polling: boolean;
  lastError?: string;
}

const stateMeta: Record<
  ReliableTask['state'],
  { label: string; color: string }
> = {
  PENDING: { label: '等待执行', color: 'processing' },
  DONE: { label: '已完成', color: 'success' },
  CANCELLED: { label: '已取消', color: 'default' },
  BLOCKED: { label: '已阻断', color: 'error' },
};

const laneMeta: Record<
  ReliableTask['executionLane'],
  { label: string; color: string }
> = {
  DEVICE: { label: '设备通道', color: 'cyan' },
  FUNDS: { label: '资金通道', color: 'gold' },
  RECYCLING: { label: '回收业务', color: 'green' },
};

const taskTypeLabels: Record<string, string> = {
  AUTHORIZE_FACTORY_SEAL: '下发工厂封签授权',
  PROCESS_INBOX: '处理可靠消息',
  SAMPLE_FULLNESS: '采集设备满溢度',
  PROVIDE_PHOTO_UPLOAD_GRANT: '下发照片上传授权',
  CONFIRM_EDGE_EVENT: '确认边缘事件',
  CREATE_NATIVE_PAYMENT: '创建 Native 充值支付',
  QUERY_NATIVE_PAYMENT: '查询 Native 充值支付',
  CLOSE_NATIVE_PAYMENT: '关闭 Native 充值支付',
  POST_RECHARGE_NET_AMOUNT: '入账充值净额',
  SUBMIT_MERCHANT_TRANSFER: '提交微信商家转账',
  QUERY_MERCHANT_TRANSFER: '查询微信商家转账',
  CREATE_MERCHANT_TRANSFER_AUTHORIZATION: '创建微信收款授权',
  QUERY_MERCHANT_TRANSFER_AUTHORIZATION: '查询微信收款授权',
  CANCEL_MERCHANT_TRANSFER: '撤销微信商家转账',
  ENSURE_DEVICE_CONFIGURATION: '收敛设备配置',
  START_DELIVERY_SESSION: '启动投递会话',
  START_CLEAN_OPERATION: '启动清运操作',
};

function failureMessage(error: unknown, fallback: string) {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : fallback;
}

function normalized(value: unknown) {
  return typeof value === 'string' && value.trim()
    ? value.trim()
    : undefined;
}

function taskTypeTitle(taskType: string) {
  return taskTypeLabels[taskType] ?? '未命名任务';
}

function canResume(task: ReliableTask) {
  return task.nextActions.includes('RESUME');
}

function isTerminalTask(task: ReliableTask) {
  return task.state !== 'PENDING';
}

function clampedPollDelay(delayMs: number) {
  return Math.max(500, Math.min(10_000, delayMs));
}

function taskAfterResumption(
  task: ReliableTask,
  accepted: ReliableTaskResumption,
): ReliableTask {
  return {
    ...task,
    state: accepted.state,
    version: accepted.version,
    nextRunAt: null,
    leased: false,
    leaseUntil: null,
    consecutiveFailureCount: 0,
    wakeVersion: task.wakeVersion + 1,
    blockedReasonCode: null,
    blockedDiagnostic: null,
    updatedAt: new Date().toISOString(),
    nextActions: [],
  };
}

function StateTag({ state }: { state: ReliableTask['state'] }) {
  const meta = stateMeta[state];
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function LaneTag({ lane }: { lane: ReliableTask['executionLane'] }) {
  const meta = laneMeta[lane];
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function TaskIdentity({ task }: { task: ReliableTask }) {
  return (
    <Space direction="vertical" size={1}>
      <Typography.Text strong>{taskTypeTitle(task.taskType)}</Typography.Text>
      <Typography.Text className="operations-task-code" type="secondary">
        {task.taskType}
      </Typography.Text>
    </Space>
  );
}

export default function OperationalGovernancePage() {
  const executeCommand = useCommandExecutor();
  const actionRef = useRef<ActionType>(null);
  const detailSequence = useRef(0);
  const selectedTaskUid = useRef<string>();
  const recoveryTimers = useRef(new Map<string, number>());
  const recoveryPollGenerations = useRef(new Map<string, number>());
  const [resumeForm] = Form.useForm<ResumeFormValues>();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [selected, setSelected] = useState<ReliableTask>();
  const [attempts, setAttempts] = useState<ReliableTaskAttempt[]>([]);
  const [attemptCursor, setAttemptCursor] = useState<string | null>(null);
  const [attemptsLoading, setAttemptsLoading] = useState(false);
  const [resumeTarget, setResumeTarget] = useState<ReliableTask>();
  const [submitting, setSubmitting] = useState(false);
  const [recoveryTrackers, setRecoveryTrackers] = useState<
    Record<string, RecoveryTracker>
  >({});

  useEffect(() => () => {
    for (const timer of recoveryTimers.current.values()) {
      window.clearTimeout(timer);
    }
    recoveryTimers.current.clear();
    for (const [taskUid, generation] of recoveryPollGenerations.current) {
      recoveryPollGenerations.current.set(taskUid, generation + 1);
    }
  }, []);

  const openTask = useCallback(async (taskUid: string) => {
    const sequence = ++detailSequence.current;
    selectedTaskUid.current = taskUid;
    setDrawerOpen(true);
    setDetailLoading(true);
    setSelected(undefined);
    setAttemptsLoading(false);
    setAttempts([]);
    setAttemptCursor(null);
    try {
      const [task, attemptPage] = await Promise.all([
        getReliableTask(taskUid),
        listReliableTaskAttempts(taskUid, { limit: 50 }),
      ]);
      if (
        sequence !== detailSequence.current
        || selectedTaskUid.current !== taskUid
      ) {
        return;
      }
      setSelected(task);
      setAttempts(attemptPage.items);
      setAttemptCursor(attemptPage.nextCursor);
    } catch (error) {
      if (sequence === detailSequence.current) {
        message.error(failureMessage(error, '可靠任务详情加载失败'));
      }
    } finally {
      if (sequence === detailSequence.current) setDetailLoading(false);
    }
  }, []);

  const closeDrawer = () => {
    detailSequence.current += 1;
    selectedTaskUid.current = undefined;
    setDrawerOpen(false);
    setDetailLoading(false);
    setSelected(undefined);
    setAttemptsLoading(false);
    setAttempts([]);
    setAttemptCursor(null);
  };

  const loadMoreAttempts = async () => {
    if (!selected || !attemptCursor || attemptsLoading) return;
    const sequence = detailSequence.current;
    const taskUid = selected.taskUid;
    const cursor = attemptCursor;
    setAttemptsLoading(true);
    try {
      const page = await listReliableTaskAttempts(taskUid, {
        cursor,
        limit: 50,
      });
      if (
        sequence !== detailSequence.current
        || selectedTaskUid.current !== taskUid
      ) {
        return;
      }
      setAttempts((current) => [...current, ...page.items]);
      setAttemptCursor(page.nextCursor);
    } catch (error) {
      if (sequence === detailSequence.current) {
        message.error(failureMessage(error, '更多执行记录加载失败'));
      }
    } finally {
      if (sequence === detailSequence.current) setAttemptsLoading(false);
    }
  };

  const startRecoveryTracking = useCallback((tracker: RecoveryTracker) => {
    const taskUid = tracker.task.taskUid;
    const previousTimer = recoveryTimers.current.get(taskUid);
    if (previousTimer !== undefined) window.clearTimeout(previousTimer);

    const generation =
      (recoveryPollGenerations.current.get(taskUid) ?? 0) + 1;
    recoveryPollGenerations.current.set(taskUid, generation);
    const pollAfterMs = clampedPollDelay(tracker.pollAfterMs);
    setRecoveryTrackers((current) => ({
      ...current,
      [taskUid]: {
        ...tracker,
        pollAfterMs,
        polling: true,
        lastError: undefined,
      },
    }));

    function schedule(delayMs: number) {
      if (recoveryPollGenerations.current.get(taskUid) !== generation) return;
      const timer = window.setTimeout(() => {
        recoveryTimers.current.delete(taskUid);
        void poll();
      }, delayMs);
      recoveryTimers.current.set(taskUid, timer);
    }

    async function poll() {
      try {
        const task = await getReliableTask(taskUid);
        if (recoveryPollGenerations.current.get(taskUid) !== generation) return;
        const terminal = isTerminalTask(task);
        setRecoveryTrackers((current) => {
          if (!current[taskUid]) return current;
          return {
            ...current,
            [taskUid]: {
              ...current[taskUid],
              task,
              polling: !terminal,
              lastError: undefined,
            },
          };
        });
        if (selectedTaskUid.current === taskUid) {
          setSelected((current) => (
            current && current.version > task.version ? current : task
          ));
        }

        if (!terminal) {
          schedule(pollAfterMs);
          return;
        }

        recoveryTimers.current.delete(taskUid);
        void actionRef.current?.reload();
        if (selectedTaskUid.current === taskUid) void openTask(taskUid);
        if (task.state === 'DONE') {
          message.success(`可靠任务 ${task.taskType} 已完成`);
        } else if (task.state === 'BLOCKED') {
          message.warning(`可靠任务 ${task.taskType} 再次阻断`);
        } else {
          message.info(`可靠任务 ${task.taskType} 已取消`);
        }
      } catch (error) {
        if (recoveryPollGenerations.current.get(taskUid) !== generation) return;
        const retryable = error instanceof ApiProblem && error.retryable;
        setRecoveryTrackers((current) => {
          if (!current[taskUid]) return current;
          return {
            ...current,
            [taskUid]: {
              ...current[taskUid],
              polling: retryable,
              lastError: failureMessage(error, '任务状态查询失败'),
            },
          };
        });
        if (retryable) {
          const retryAfterMs = error.retryAfterSeconds
            ? error.retryAfterSeconds * 1_000
            : Math.min(30_000, pollAfterMs * 2);
          schedule(Math.max(pollAfterMs, retryAfterMs));
        }
      }
    }

    schedule(pollAfterMs);
  }, [openTask]);

  const dismissRecoveryTracker = (taskUid: string) => {
    const timer = recoveryTimers.current.get(taskUid);
    if (timer !== undefined) window.clearTimeout(timer);
    recoveryTimers.current.delete(taskUid);
    recoveryPollGenerations.current.set(
      taskUid,
      (recoveryPollGenerations.current.get(taskUid) ?? 0) + 1,
    );
    setRecoveryTrackers((current) => {
      const next = { ...current };
      delete next[taskUid];
      return next;
    });
  };

  const openResume = (task: ReliableTask) => {
    if (!canResume(task)) {
      message.warning('服务端没有授权这条任务使用通用恢复');
      return;
    }
    setResumeTarget(task);
  };

  const submitResume = async () => {
    if (!resumeTarget) return;
    let values: ResumeFormValues;
    try {
      values = await resumeForm.validateFields();
    } catch {
      return;
    }
    const payload: ResumeReliableTaskRequest = {
      expectedVersion: resumeTarget.version,
      causeFixedConfirmed: true,
      reason: values.reason.trim(),
    };
    const taskUid = resumeTarget.taskUid;
    setSubmitting(true);
    try {
      const accepted = await executeCommand(
        commandKey('reliable-task-resume', taskUid, payload),
        (intent) => resumeReliableTask(taskUid, payload, intent),
      );
      startRecoveryTracking({
        task: taskAfterResumption(resumeTarget, accepted),
        pollAfterMs: accepted.recommendedPollAfterMs,
        polling: true,
      });
      setResumeTarget(undefined);
      message.success('原可靠任务已恢复，系统将按原业务身份继续收敛');
      await actionRef.current?.reload();
      if (selectedTaskUid.current === taskUid) await openTask(taskUid);
    } catch (error) {
      message.error(failureMessage(error, '可靠任务恢复失败'));
      if (error instanceof ApiProblem && error.isVersionConflict) {
        setResumeTarget(undefined);
        await actionRef.current?.reload();
        if (selectedTaskUid.current === taskUid) await openTask(taskUid);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const columns: ProColumns<ReliableTask>[] = [
    {
      title: '任务类型',
      dataIndex: 'taskType',
      width: 270,
      render: (_, task) => (
        <Typography.Link onClick={() => void openTask(task.taskUid)}>
          <TaskIdentity task={task} />
        </Typography.Link>
      ),
    },
    {
      title: '状态',
      dataIndex: 'state',
      valueType: 'select',
      initialValue: 'BLOCKED',
      valueEnum: {
        PENDING: { text: '等待执行' },
        DONE: { text: '已完成' },
        CANCELLED: { text: '已取消' },
        BLOCKED: { text: '已阻断' },
      },
      width: 105,
      render: (_, task) => <StateTag state={task.state} />,
    },
    {
      title: '执行通道',
      dataIndex: 'executionLane',
      valueType: 'select',
      valueEnum: {
        DEVICE: { text: '设备通道' },
        FUNDS: { text: '资金通道' },
        RECYCLING: { text: '回收业务' },
      },
      width: 120,
      render: (_, task) => <LaneTag lane={task.executionLane} />,
    },
    {
      title: '目标',
      dataIndex: 'targetKey',
      width: 220,
      render: (_, task) => (
        <Space direction="vertical" size={1}>
          <Typography.Text className="operations-target-key" copyable>
            {task.targetKey ?? '—'}
          </Typography.Text>
          <Typography.Text type="secondary">
            {task.targetType ?? '未指定目标类型'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '尝试',
      dataIndex: 'attemptCount',
      search: false,
      width: 95,
      render: (_, task) => (
        <Space direction="vertical" size={1}>
          <Typography.Text>{task.attemptCount} 次</Typography.Text>
          {task.consecutiveFailureCount > 0 && (
            <Typography.Text type="danger">
              连续失败 {task.consecutiveFailureCount}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '最近更新',
      dataIndex: 'updatedAt',
      search: false,
      width: 170,
      render: (_, task) => formatShanghaiTime(task.updatedAt),
    },
    {
      title: '操作',
      valueType: 'option',
      fixed: 'right',
      width: 150,
      render: (_, task) => (
        <Space size={4}>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => void openTask(task.taskUid)}
          >
            详情
          </Button>
          {canResume(task) && (
            <Button
              type="link"
              danger
              size="small"
              icon={<SyncOutlined />}
              onClick={() => openResume(task)}
            >
              恢复
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const attemptColumns = [
    {
      title: '序号',
      dataIndex: 'attemptNo',
      width: 70,
      render: (value: number) => `#${value}`,
    },
    {
      title: '动作',
      dataIndex: 'actionKind',
      width: 150,
    },
    {
      title: '技术结果',
      dataIndex: 'technicalResult',
      width: 150,
      render: (value: string | null) => (
        value ? <Tag>{value}</Tag> : <Typography.Text type="secondary">等待结果</Typography.Text>
      ),
    },
    {
      title: '外部响应',
      key: 'externalResult',
      width: 170,
      render: (_: unknown, attempt: ReliableTaskAttempt) => (
        <Space direction="vertical" size={1}>
          <Typography.Text>
            {attempt.httpStatus === null ? 'HTTP —' : `HTTP ${attempt.httpStatus}`}
          </Typography.Text>
          {attempt.externalApiErrorCode && (
            <Typography.Text type="danger">
              {attempt.externalApiErrorCode}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '耗时',
      dataIndex: 'durationMs',
      width: 90,
      render: (value: number | null) => (value === null ? '—' : `${value} ms`),
    },
    {
      title: '任务领取',
      dataIndex: 'claimedAt',
      width: 175,
      render: (value: string) => formatShanghaiTime(value),
    },
    {
      title: '可能开始外调',
      dataIndex: 'externalCallMayHaveStartedAt',
      width: 175,
      render: (value: string | null) => (
        value ? formatShanghaiTime(value) : '—'
      ),
    },
    {
      title: '结果落库',
      dataIndex: 'resultRecordedAt',
      width: 175,
      render: (value: string | null) => (
        value ? formatShanghaiTime(value) : '—'
      ),
    },
    {
      title: '诊断',
      dataIndex: 'diagnostic',
      width: 260,
      ellipsis: true,
      render: (value: string | null) => (
        value
          ? <Tooltip title={value}><Typography.Text>{value}</Typography.Text></Tooltip>
          : '—'
      ),
    },
  ];

  return (
    <PageContainer
      {...pageHeader(
        '运营治理',
        '集中查看可靠任务，只恢复服务端明确判定为安全的失败操作。',
      )}
      className="operations-governance-page"
    >
      <section className="operations-control-brief" aria-label="可靠任务恢复边界">
        <div>
          <Typography.Text className="operations-eyebrow">
            CONTROL PLANE · 人工恢复台
          </Typography.Text>
          <Typography.Title level={4}>先确认原因，再唤醒原任务</Typography.Title>
          <Typography.Paragraph type="secondary">
            恢复会复用原业务身份与幂等键，不会创建替代订单、替代设备命令或重复资金意图。
          </Typography.Paragraph>
        </div>
        <div className="operations-legend" aria-label="状态说明">
          <span><i className="operations-dot operations-dot-blocked" /> 已阻断</span>
          <span><i className="operations-dot operations-dot-safe" /> 允许恢复</span>
          <span><i className="operations-dot operations-dot-readonly" /> 仅可查看</span>
        </div>
      </section>

      <Alert
        className="operations-boundary-alert"
        type="info"
        showIcon
        icon={<SafetyCertificateOutlined />}
        message="操作权限由后端实时决定"
        description="只有 nextActions 包含 RESUME 的已阻断任务才会显示“恢复”。没有按钮表示该任务必须使用专用恢复流程，或当前状态已经变化。"
      />

      {Object.values(recoveryTrackers).length > 0 && (
        <section
          className="operations-recovery-panel"
          aria-label="恢复任务跟踪"
        >
          <div className="operations-recovery-panel-title">
            <Typography.Text strong>恢复任务跟踪</Typography.Text>
            <Typography.Text type="secondary">
              即使任务因当前筛选移出列表，仍会查询到完成、再次阻断或取消。
            </Typography.Text>
          </div>
          <div className="operations-recovery-trackers">
            {Object.values(recoveryTrackers).map((tracker) => {
              const terminal = isTerminalTask(tracker.task);
              return (
                <div
                  className="operations-recovery-tracker"
                  key={tracker.task.taskUid}
                >
                  <TaskIdentity task={tracker.task} />
                  <StateTag state={tracker.task.state} />
                  <div className="operations-recovery-progress">
                    <Typography.Text>
                      {tracker.polling
                        ? '正在等待后端收敛并持续查询'
                        : terminal
                          ? '已到达终态'
                          : '状态查询已停止'}
                    </Typography.Text>
                    {tracker.lastError && (
                      <Typography.Text type="danger">
                        {tracker.lastError}
                      </Typography.Text>
                    )}
                  </div>
                  <Space size={4}>
                    <Button
                      type="link"
                      size="small"
                      onClick={() => void openTask(tracker.task.taskUid)}
                    >
                      查看详情
                    </Button>
                    {!tracker.polling && !terminal && (
                      <Button
                        type="link"
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={() => startRecoveryTracking(tracker)}
                      >
                        继续查询
                      </Button>
                    )}
                    {!tracker.polling && (
                      <Button
                        type="link"
                        size="small"
                        onClick={() => dismissRecoveryTracker(
                          tracker.task.taskUid,
                        )}
                      >
                        关闭跟踪
                      </Button>
                    )}
                  </Space>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <ProTable<ReliableTask>
        {...proTableConfig}
        actionRef={actionRef}
        rowKey="taskUid"
        columns={columns}
        headerTitle="可靠任务"
        request={async (params) => {
          try {
            const query: ReliableTaskListParams = {
              page: params.current,
              pageSize: params.pageSize,
              state: params.state as ReliableTaskListParams['state'],
              executionLane:
                params.executionLane as ReliableTaskListParams['executionLane'],
              taskType: normalized(params.taskType),
              targetKey: normalized(params.targetKey),
            };
            const page = await listReliableTasks(query);
            return {
              data: page.items,
              total: page.total,
              success: true,
            };
          } catch (error) {
            message.error(failureMessage(error, '可靠任务列表加载失败'));
            return { data: [], total: 0, success: false };
          }
        }}
      />

      <Drawer
        title={selected ? <TaskIdentity task={selected} /> : '可靠任务详情'}
        width={880}
        open={drawerOpen}
        onClose={closeDrawer}
        extra={selected && (
          <Space>
            <Button
              icon={<ReloadOutlined />}
              loading={detailLoading}
              onClick={() => void openTask(selected.taskUid)}
            >
              刷新
            </Button>
            {canResume(selected) && (
              <Button
                danger
                type="primary"
                icon={<SyncOutlined />}
                onClick={() => openResume(selected)}
              >
                恢复任务
              </Button>
            )}
          </Space>
        )}
      >
        {detailLoading && !selected ? (
          <div className="operations-detail-loading"><Spin /></div>
        ) : selected ? (
          <>
            {selected.state === 'BLOCKED' && (
              <Alert
                className="operations-blocker"
                type={canResume(selected) ? 'warning' : 'error'}
                showIcon
                message={selected.blockedReasonCode ?? '任务已阻断'}
                description={selected.blockedDiagnostic ?? '服务端没有提供更多诊断信息'}
              />
            )}

            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="状态">
                <StateTag state={selected.state} />
              </Descriptions.Item>
              <Descriptions.Item label="执行通道">
                <LaneTag lane={selected.executionLane} />
              </Descriptions.Item>
              <Descriptions.Item label="任务 UID" span={2}>
                <Typography.Text className="operations-task-code" copyable>
                  {selected.taskUid}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="任务类别">
                {selected.taskKind}
              </Descriptions.Item>
              <Descriptions.Item label="作用域">
                {selected.scopeKind}
              </Descriptions.Item>
              <Descriptions.Item label="目标类型">
                {selected.targetType ?? '—'}
              </Descriptions.Item>
              <Descriptions.Item label="目标键">
                <Typography.Text copyable>{selected.targetKey ?? '—'}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="任务版本">
                {selected.version}
              </Descriptions.Item>
              <Descriptions.Item label="唤醒版本">
                {selected.handledWakeVersion} / {selected.wakeVersion}
              </Descriptions.Item>
              <Descriptions.Item label="累计尝试">
                {selected.attemptCount}
              </Descriptions.Item>
              <Descriptions.Item label="连续失败">
                {selected.consecutiveFailureCount}
              </Descriptions.Item>
              <Descriptions.Item label="下次执行">
                {selected.nextRunAt ? formatShanghaiTime(selected.nextRunAt) : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="租约">
                {selected.leased
                  ? `占用至 ${selected.leaseUntil ? formatShanghaiTime(selected.leaseUntil) : '未知'}`
                  : '未占用'}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {formatShanghaiTime(selected.createdAt)}
              </Descriptions.Item>
              <Descriptions.Item label="更新时间">
                {formatShanghaiTime(selected.updatedAt)}
              </Descriptions.Item>
            </Descriptions>

            <Divider orientation="left">执行尝试</Divider>
            <Table<ReliableTaskAttempt>
              rowKey="attemptUid"
              size="small"
              columns={attemptColumns}
              dataSource={attempts}
              pagination={false}
              loading={attemptsLoading}
              scroll={{ x: 1_450 }}
              locale={{ emptyText: '该任务尚未产生执行尝试' }}
            />
            {attemptCursor && (
              <div className="operations-load-more">
                <Button loading={attemptsLoading} onClick={() => void loadMoreAttempts()}>
                  加载更早记录
                </Button>
              </div>
            )}
          </>
        ) : (
          <Empty description="任务详情不可用" />
        )}
      </Drawer>

      <Modal
        title="恢复可靠任务"
        open={!!resumeTarget}
        okText="确认恢复原任务"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: submitting }}
        cancelButtonProps={{ disabled: submitting }}
        closable={!submitting}
        maskClosable={false}
        onOk={() => void submitResume()}
        onCancel={() => {
          if (!submitting) setResumeTarget(undefined);
        }}
        afterClose={() => resumeForm.resetFields()}
      >
        <Alert
          className="operations-resume-warning"
          type="warning"
          showIcon
          message="这不是重新创建业务"
          description="系统会唤醒原可靠任务。设备与资金任务仍会执行各自的幂等检查、当前事实复核和外部边界校验。"
        />
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="任务">
            {resumeTarget ? taskTypeTitle(resumeTarget.taskType) : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="阻断原因">
            {resumeTarget?.blockedReasonCode ?? '未提供'}
          </Descriptions.Item>
          <Descriptions.Item label="目标">
            {resumeTarget?.targetKey ?? '—'}
          </Descriptions.Item>
        </Descriptions>
        <Form<ResumeFormValues>
          form={resumeForm}
          layout="vertical"
          requiredMark="optional"
          initialValues={{ causeFixedConfirmed: false, reason: '' }}
        >
          <Form.Item
            name="causeFixedConfirmed"
            valuePropName="checked"
            rules={[{
              validator: (_, checked) => (
                checked
                  ? Promise.resolve()
                  : Promise.reject(new Error('请先确认阻断原因已经排除'))
              ),
            }]}
          >
            <Checkbox>我已核实并排除上述阻断原因</Checkbox>
          </Form.Item>
          <Form.Item
            name="reason"
            label="恢复说明"
            rules={[{
              validator: (_, value: string | undefined) => {
                const reason = value?.trim() ?? '';
                if (!reason) {
                  return Promise.reject(new Error(
                    '请填写本次恢复的事实依据',
                  ));
                }
                if (reason.length < 5) {
                  return Promise.reject(new Error(
                    '恢复说明至少 5 个字符',
                  ));
                }
                if (reason.length > 500) {
                  return Promise.reject(new Error(
                    '恢复说明不能超过 500 个字符',
                  ));
                }
                return Promise.resolve();
              },
            }]}
          >
            <Input.TextArea
              rows={4}
              maxLength={500}
              showCount
              placeholder="例如：OneNet 物模型已重新导入，已核对服务标识和设备在线状态"
            />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
