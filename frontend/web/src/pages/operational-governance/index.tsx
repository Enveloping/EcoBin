import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
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
  ExclamationCircleOutlined,
  EyeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import {
  getReliableTask,
  listReliableTaskAttempts,
  listReliableTaskTypes,
  listReliableTasks,
  resumeReliableTask,
  type ReliableTask,
  type ReliableTaskAttempt,
  type ReliableTaskListParams,
  type ReliableTaskResumption,
  type ReliableTaskType,
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

const taskKindLabels: Record<string, string> = {
  BUSINESS_INTENT: '业务动作',
  INBOX_PROCESSING: '入站消息处理',
  TIMER: '定时任务',
  RECONCILIATION: '状态核对',
};

const scopeKindLabels: Record<string, string> = {
  PLATFORM: '平台范围',
  TENANT: '租户范围',
  ORGANIZATION: '机构范围',
  UNRESOLVED: '归属尚未解析',
};

const actionKindLabels: Record<string, string> = {
  PROCESS: '处理消息或业务事实',
  SUBMIT: '提交外部业务请求',
  QUERY: '查询外部状态',
  CLOSE: '关闭外部资源',
  CANCEL: '撤销或核对历史撤销',
};

const technicalResultLabels: Record<string, string> = {
  TECHNICAL_SUCCESS: '技术执行成功',
  NO_ACTION_REQUIRED: '无需再次执行',
  RETRYABLE_FAILURE: '暂时失败，可自动重试',
  OUTCOME_UNKNOWN: '外部结果尚不明确',
  PERMANENT_TECHNICAL_FAILURE: '永久技术失败',
};

const targetTypeLabels: Record<string, string> = {
  DEVICE: '设备',
  DEVICE_ASSET: '永久设备资产',
  DEVICE_CONFIGURATION: '设备配置',
  DEVICE_ENTRY_URL: '设备公开入口',
  DEVICE_EVENT: '设备事件',
  DELIVERY_SESSION: '投递会话',
  DELIVERY_ORDER: '投递订单',
  CLEAN_OPERATION: '清运操作',
  EMPTY_BAG_BASELINE: '空袋重量基线',
  PHOTO_UPLOAD_GRANT: '照片上传授权',
  REMOTE_SUPPORT_SESSION: '远程支持会话',
  MCU_FIRMWARE_DEPLOYMENT: 'MCU 固件部署',
  NATIVE_PAYMENT: '微信充值支付单',
  RECHARGE_ORDER: '充值订单',
  WITHDRAWAL_ORDER: '提现订单',
  WECHAT_TRANSFER_AUTHORIZATION: '微信收款授权',
  INBOX_MESSAGE: '可靠入站消息',
};

const blockedReasonLabels: Record<string, string> = {
  AUTO_RETRY_EXHAUSTED: '自动重试次数已用尽',
  DEVICE_TASK_BLOCKED: '设备任务需要人工排查',
  FUNDS_TASK_BLOCKED: '资金任务需要人工排查',
  RECYCLING_TASK_BLOCKED: '回收业务任务需要人工排查',
  MANUAL_INTERVENTION_REQUIRED: '需要人工核对当前事实',
};

const termExplanations = {
  reliableTask:
    '可靠任务是系统必须最终完成、取消或明确阻断的一次后台工作。系统会保存任务身份和每次尝试，临时故障不会直接丢失业务动作。',
  taskType:
    '任务类型说明系统要完成哪一种业务工作。中文名称用于运营识别，大写英文代码用于后端精确匹配。',
  taskKind:
    '任务类别表示任务为什么产生：业务动作会改变业务状态，入站消息处理消费外部消息，定时任务由系统计划触发，状态核对用于收敛外部事实。',
  state:
    '等待执行表示系统仍会处理；已完成和已取消是终态；已阻断表示自动处理已经停止，需要排除原因或走专用处置流程。',
  executionLane:
    '执行通道把设备、资金和回收工作分给相互隔离的后台执行器，避免某一类故障拖慢其他业务。',
  target:
    '目标由“目标类型 + 目标键”组成，指出这条任务正在处理哪一台设备、哪一笔订单或哪一条消息。',
  attempt:
    '执行尝试是后台执行器实际领取并处理任务的一次记录。任务可有多次尝试，但会沿用同一业务身份，尝试次数不等于重复创建订单。',
  taskUid:
    '任务 UID 是这条可靠任务不可变的唯一编号，查看日志或向管理员反馈问题时可用它精确定位。',
  version:
    '任务版本在每次状态修改后递增，用于防止两个操作同时覆盖彼此的结果。恢复操作必须提交当前版本。',
  wakeVersion:
    '右侧是已收到的唤醒版本，左侧是执行器已经处理的唤醒版本；两者不相等时，系统还需要再检查一次任务。',
  nextRunAt:
    '等待执行的任务会在该时间之后再次被后台领取；为空通常表示任务已到终态、被阻断，或正在由执行器处理。',
  lease:
    '租约是某个后台执行器对任务的短期独占权。租约有效时其他执行器不会并行执行同一任务，过期后系统可以安全接管。',
  actionKind:
    '动作表示本次尝试准备做什么，例如提交、查询、关闭或处理消息；它不代表动作已经成功。',
  technicalResult:
    '技术结果记录本次尝试是否成功、无需操作、可重试、结果未知或永久失败；最终业务状态仍以任务状态和外部证据为准。',
  externalResponse:
    '只有确实调用 OneNet、微信等外部系统时才可能有 HTTP 状态、外部错误码或外部请求编号；请求编号可用于向渠道查询该次调用，任一字段为空都不等于成功。',
  duration:
    '从本次尝试开始处理到结果写入所记录的耗时；它用于排查性能，不决定业务是否成功。',
  claimedAt:
    '后台执行器取得任务租约的时间，只证明开始处理，不证明已经调用外部系统。',
  externalCallAt:
    '系统在可能越过 OneNet、微信等外部边界前记录该时间；存在该值表示外部副作用可能已经发生，恢复前必须核对。',
  resultRecordedAt:
    '本次尝试的技术结果写入数据库的时间；为空表示该尝试尚未留下最终技术结论。',
  diagnostic:
    '经过脱敏的技术诊断，用于解释失败位置。它可能是内部英文信息，但不能代替订单、设备或渠道的业务事实。',
  scopeKind:
    '作用域表示任务属于整个平台、某个租户还是某个机构，并决定后台读取和修改数据时采用哪一层隔离边界。',
  recovery:
    '恢复会重新唤醒同一条任务，并可能首次完成原任务本来应产生的业务结果；系统复用原任务身份和防重复边界，不会创建重复或替代的业务结果。按钮是否出现由后端基于任务类型和当前事实决定。',
} as const;

function failureMessage(error: unknown, fallback: string) {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : fallback;
}

function normalized(value: unknown) {
  if (Array.isArray(value)) return normalized(value[0]);
  return typeof value === 'string' && value.trim()
    ? value.trim()
    : undefined;
}

function taskTypeTitle(taskType: string, metadata?: ReliableTaskType) {
  return metadata?.displayName
    ?? `未获取到中文说明（${taskType}）`;
}

function ExplainedLabel({
  label,
  explanation,
}: {
  label: ReactNode;
  explanation: string;
}) {
  return (
    <span className="operations-explained-label">
      <span>{label}</span>
      <Tooltip title={explanation} placement="top">
        <ExclamationCircleOutlined
          className="operations-explanation-icon"
          role="img"
          aria-label={`${typeof label === 'string' ? label : '该字段'}说明`}
          tabIndex={0}
        />
      </Tooltip>
    </span>
  );
}

function LabeledCode({
  value,
  labels,
  fallbackLabel = '其他系统值',
}: {
  value: string | null;
  labels: Record<string, string>;
  fallbackLabel?: string;
}) {
  if (!value) return <>—</>;
  return (
    <Space direction="vertical" size={0}>
      <Typography.Text>{labels[value] ?? fallbackLabel}</Typography.Text>
      <Typography.Text className="operations-task-code" type="secondary">
        {value}
      </Typography.Text>
    </Space>
  );
}

function BlockedReason({ code }: { code: string | null }) {
  if (!code) return <>服务端未提供原因代码</>;
  return (
    <LabeledCode
      value={code}
      labels={blockedReasonLabels}
      fallbackLabel="后端已停止自动处理"
    />
  );
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

function TaskIdentity({
  task,
  metadata,
}: {
  task: ReliableTask;
  metadata?: ReliableTaskType;
}) {
  return (
    <Space direction="vertical" size={1}>
      <Typography.Text strong>
        <ExplainedLabel
          label={taskTypeTitle(task.taskType, metadata)}
          explanation={metadata?.description ?? termExplanations.taskType}
        />
      </Typography.Text>
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
  const taskTypeCatalogGeneration = useRef(0);
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
  const [taskTypes, setTaskTypes] = useState<ReliableTaskType[]>([]);
  const [taskTypesLoading, setTaskTypesLoading] = useState(true);
  const [taskTypesError, setTaskTypesError] = useState<string>();

  const taskTypeByCode = useMemo(() => Object.fromEntries(
    taskTypes.map((item) => [item.taskType, item]),
  ) as Record<string, ReliableTaskType>, [taskTypes]);

  const taskTypeOptions = useMemo(() => taskTypes.map((item) => ({
    value: item.taskType,
    label: (
      <span className="operations-task-type-option">
        <span className="operations-task-type-option-name">
          {item.displayName}
        </span>
        <Tooltip title={item.description} placement="right">
          <ExclamationCircleOutlined
            className="operations-explanation-icon"
            aria-label={`${item.displayName}说明`}
          />
        </Tooltip>
        <span className="operations-task-type-option-code">
          {item.taskType}
        </span>
      </span>
    ),
    searchText: `${item.displayName} ${item.taskType} ${item.description}`,
  })), [taskTypes]);

  const loadTaskTypes = useCallback(async () => {
    const generation = ++taskTypeCatalogGeneration.current;
    setTaskTypesLoading(true);
    setTaskTypesError(undefined);
    try {
      const items = await listReliableTaskTypes();
      if (generation !== taskTypeCatalogGeneration.current) return;
      setTaskTypes(items);
    } catch (error) {
      if (generation !== taskTypeCatalogGeneration.current) return;
      setTaskTypesError(failureMessage(
        error,
        '任务类型目录加载失败',
      ));
    } finally {
      if (generation === taskTypeCatalogGeneration.current) {
        setTaskTypesLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadTaskTypes();
    return () => {
      taskTypeCatalogGeneration.current += 1;
    };
  }, [loadTaskTypes]);

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
        const taskName = taskTypeTitle(
          task.taskType,
          taskTypeByCode[task.taskType],
        );
        if (task.state === 'DONE') {
          message.success(`可靠任务“${taskName}”已完成`);
        } else if (task.state === 'BLOCKED') {
          message.warning(`可靠任务“${taskName}”再次阻断`);
        } else {
          message.info(`可靠任务“${taskName}”已取消`);
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
  }, [openTask, taskTypeByCode]);

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
      message.success('原可靠任务已恢复，系统将按最新事实继续处理');
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
      title: (
        <ExplainedLabel
          label="任务类型"
          explanation={termExplanations.taskType}
        />
      ),
      dataIndex: 'taskType',
      width: 270,
      valueType: 'select',
      fieldProps: {
        allowClear: true,
        showSearch: true,
        mode: 'tags',
        maxCount: 1,
        loading: taskTypesLoading,
        placeholder: '选择或输入完整任务类型代码',
        options: taskTypeOptions,
        filterOption: (
          input: string,
          option?: { searchText?: string; value?: string },
        ) => (
          (option?.searchText ?? option?.value ?? '')
            .toLocaleLowerCase()
            .includes(input.trim().toLocaleLowerCase())
        ),
      },
      render: (_, task) => (
        <Typography.Link onClick={() => void openTask(task.taskUid)}>
          <TaskIdentity
            task={task}
            metadata={taskTypeByCode[task.taskType]}
          />
        </Typography.Link>
      ),
    },
    {
      title: (
        <ExplainedLabel
          label="状态"
          explanation={termExplanations.state}
        />
      ),
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
      title: (
        <ExplainedLabel
          label="执行通道"
          explanation={termExplanations.executionLane}
        />
      ),
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
      title: (
        <ExplainedLabel
          label="任务类别"
          explanation={termExplanations.taskKind}
        />
      ),
      dataIndex: 'taskKind',
      valueType: 'select',
      hideInTable: true,
      valueEnum: {
        BUSINESS_INTENT: { text: '业务动作' },
        INBOX_PROCESSING: { text: '入站消息处理' },
        TIMER: { text: '定时任务' },
        RECONCILIATION: { text: '状态核对' },
      },
    },
    {
      title: (
        <ExplainedLabel
          label="目标"
          explanation={termExplanations.target}
        />
      ),
      dataIndex: 'targetKey',
      width: 220,
      render: (_, task) => (
        <Space direction="vertical" size={1}>
          <Typography.Text className="operations-target-key" copyable>
            {task.targetKey ?? '—'}
          </Typography.Text>
          {task.targetType ? (
            <Typography.Text type="secondary">
              {targetTypeLabels[task.targetType] ?? '其他业务目标'} ·{' '}
              <span className="operations-task-code">{task.targetType}</span>
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">未指定目标类型</Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: (
        <ExplainedLabel
          label="尝试"
          explanation={termExplanations.attempt}
        />
      ),
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
      title: (
        <ExplainedLabel
          label="动作"
          explanation={termExplanations.actionKind}
        />
      ),
      dataIndex: 'actionKind',
      width: 175,
      render: (value: string) => (
        <LabeledCode
          value={value}
          labels={actionKindLabels}
          fallbackLabel="其他系统动作"
        />
      ),
    },
    {
      title: (
        <ExplainedLabel
          label="技术结果"
          explanation={termExplanations.technicalResult}
        />
      ),
      dataIndex: 'technicalResult',
      width: 190,
      render: (value: string | null) => (
        value ? (
          <Space direction="vertical" size={0}>
            <Tag>{technicalResultLabels[value] ?? '其他技术结果'}</Tag>
            <Typography.Text
              className="operations-task-code"
              type="secondary"
            >
              {value}
            </Typography.Text>
          </Space>
        ) : <Typography.Text type="secondary">等待结果</Typography.Text>
      ),
    },
    {
      title: (
        <ExplainedLabel
          label="外部响应"
          explanation={termExplanations.externalResponse}
        />
      ),
      key: 'externalResult',
      width: 250,
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
          {attempt.externalRequestId && (
            <Typography.Text
              type="secondary"
              copyable={{ text: attempt.externalRequestId }}
            >
              {`请求 ${attempt.externalRequestId}`}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: (
        <ExplainedLabel
          label="耗时"
          explanation={termExplanations.duration}
        />
      ),
      dataIndex: 'durationMs',
      width: 90,
      render: (value: number | null) => (value === null ? '—' : `${value} ms`),
    },
    {
      title: (
        <ExplainedLabel
          label="任务领取"
          explanation={termExplanations.claimedAt}
        />
      ),
      dataIndex: 'claimedAt',
      width: 175,
      render: (value: string) => formatShanghaiTime(value),
    },
    {
      title: (
        <ExplainedLabel
          label="可能开始外调"
          explanation={termExplanations.externalCallAt}
        />
      ),
      dataIndex: 'externalCallMayHaveStartedAt',
      width: 175,
      render: (value: string | null) => (
        value ? formatShanghaiTime(value) : '—'
      ),
    },
    {
      title: (
        <ExplainedLabel
          label="结果落库"
          explanation={termExplanations.resultRecordedAt}
        />
      ),
      dataIndex: 'resultRecordedAt',
      width: 175,
      render: (value: string | null) => (
        value ? formatShanghaiTime(value) : '—'
      ),
    },
    {
      title: (
        <ExplainedLabel
          label="诊断"
          explanation={termExplanations.diagnostic}
        />
      ),
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
            运营控制台 · 人工恢复
          </Typography.Text>
          <Typography.Title level={4}>先确认原因，再唤醒原任务</Typography.Title>
          <Typography.Paragraph type="secondary">
            恢复可能首次完成原任务本来应产生的业务结果；系统会沿用原任务及其防重复身份，不会创建重复或替代的业务结果。
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
        message={(
          <ExplainedLabel
            label="恢复权限由后端实时决定"
            explanation={termExplanations.recovery}
          />
        )}
        description="只有后端明确判定可以安全恢复的已阻断任务才会显示“恢复”。没有按钮表示该任务必须使用专用处置流程，或当前状态已经变化。"
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
                  <TaskIdentity
                    task={tracker.task}
                    metadata={taskTypeByCode[tracker.task.taskType]}
                  />
                  <StateTag state={tracker.task.state} />
                  <div className="operations-recovery-progress">
                    <Typography.Text>
                      {tracker.polling
                        ? '正在等待后端完成处理并持续查询'
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

      {taskTypesError && (
        <Alert
          className="operations-catalog-alert"
          type="warning"
          showIcon
          message="任务类型目录暂时不可用"
          description={`仍可在任务类型筛选框中输入完整英文代码并回车查询。失败原因：${taskTypesError}`}
          action={(
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={taskTypesLoading}
              onClick={() => void loadTaskTypes()}
            >
              重新加载任务类型目录
            </Button>
          )}
        />
      )}

      <ProTable<ReliableTask>
        {...proTableConfig}
        actionRef={actionRef}
        rowKey="taskUid"
        columns={columns}
        headerTitle={(
          <ExplainedLabel
            label="可靠任务"
            explanation={termExplanations.reliableTask}
          />
        )}
        request={async (params) => {
          try {
            const query: ReliableTaskListParams = {
              page: params.current,
              pageSize: params.pageSize,
              state: params.state as ReliableTaskListParams['state'],
              executionLane:
                params.executionLane as ReliableTaskListParams['executionLane'],
              taskKind: normalized(params.taskKind) as
                ReliableTaskListParams['taskKind'],
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
        title={selected ? (
          <TaskIdentity
            task={selected}
            metadata={taskTypeByCode[selected.taskType]}
          />
        ) : '可靠任务详情'}
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
                message={(
                  <Space>
                    <Typography.Text strong>任务已阻断</Typography.Text>
                    <BlockedReason code={selected.blockedReasonCode} />
                  </Space>
                )}
                description={(
                  <Space direction="vertical" size={2}>
                    <ExplainedLabel
                      label="阻断诊断"
                      explanation={termExplanations.diagnostic}
                    />
                    <Typography.Text>
                      {selected.blockedDiagnostic
                        ?? '服务端没有提供更多诊断信息'}
                    </Typography.Text>
                  </Space>
                )}
              />
            )}

            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="状态"
                  explanation={termExplanations.state}
                />
              )}>
                <StateTag state={selected.state} />
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="执行通道"
                  explanation={termExplanations.executionLane}
                />
              )}>
                <LaneTag lane={selected.executionLane} />
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="任务 UID"
                  explanation={termExplanations.taskUid}
                />
              )} span={2}>
                <Typography.Text className="operations-task-code" copyable>
                  {selected.taskUid}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="任务类别"
                  explanation={termExplanations.taskKind}
                />
              )}>
                <LabeledCode value={selected.taskKind} labels={taskKindLabels} />
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="作用域"
                  explanation={termExplanations.scopeKind}
                />
              )}>
                <LabeledCode value={selected.scopeKind} labels={scopeKindLabels} />
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="目标类型"
                  explanation={termExplanations.target}
                />
              )}>
                <LabeledCode
                  value={selected.targetType}
                  labels={targetTypeLabels}
                  fallbackLabel="其他业务目标"
                />
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="目标键"
                  explanation={termExplanations.target}
                />
              )}>
                <Typography.Text copyable>{selected.targetKey ?? '—'}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="任务版本"
                  explanation={termExplanations.version}
                />
              )}>
                {selected.version}
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="已处理 / 已唤醒版本"
                  explanation={termExplanations.wakeVersion}
                />
              )}>
                {selected.handledWakeVersion} / {selected.wakeVersion}
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="累计尝试"
                  explanation={termExplanations.attempt}
                />
              )}>
                {selected.attemptCount}
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="连续失败"
                  explanation="从最近一次成功或人工恢复之后连续失败的次数；达到自动上限时任务会进入已阻断状态。"
                />
              )}>
                {selected.consecutiveFailureCount}
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="下次执行"
                  explanation={termExplanations.nextRunAt}
                />
              )}>
                {selected.nextRunAt ? formatShanghaiTime(selected.nextRunAt) : '—'}
              </Descriptions.Item>
              <Descriptions.Item label={(
                <ExplainedLabel
                  label="执行租约"
                  explanation={termExplanations.lease}
                />
              )}>
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

            <Divider orientation="left">
              <ExplainedLabel
                label="执行尝试"
                explanation={termExplanations.attempt}
              />
            </Divider>
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
          description="系统会唤醒原可靠任务，它可能首次完成原本应产生的订单、设备命令或提现请求；系统会复用原任务身份，并继续执行防重复检查、当前事实复核和外部系统边界校验。"
        />
        <Descriptions size="small" column={1}>
          <Descriptions.Item label={(
            <ExplainedLabel
              label="任务"
              explanation={termExplanations.taskType}
            />
          )}>
            {resumeTarget
              ? taskTypeTitle(
                resumeTarget.taskType,
                taskTypeByCode[resumeTarget.taskType],
              )
              : '—'}
          </Descriptions.Item>
          <Descriptions.Item label={(
            <ExplainedLabel
              label="阻断原因"
              explanation="这是后端停止自动处理时保存的原因。中文名称用于理解，英文代码用于精确查询日志。"
            />
          )}>
            <BlockedReason code={resumeTarget?.blockedReasonCode ?? null} />
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
