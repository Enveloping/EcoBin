import { useEffect, useRef, useState } from 'react';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  Button,
  Descriptions,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  message,
} from 'antd';
import {
  approveBusinessRelease,
  cancelBusinessRolloutDeployment,
  createBusinessRelease,
  createBusinessRollout,
  getBusinessRelease,
  getBusinessReleaseReadiness,
  getBusinessRollout,
  listBusinessReleases,
  listBusinessRollouts,
  resumeBusinessRelease,
  retireBusinessRelease,
  startBusinessRolloutValidation,
  stopBusinessRollout,
  suspendBusinessRelease,
  uploadBusinessReleaseArtifacts,
  verifyBusinessRelease,
  type BusinessDeployment,
  type BusinessRelease,
  type BusinessReleaseAction,
  type BusinessReleaseReadiness,
  type BusinessRollout,
  type BusinessRolloutAction,
  type CreateBusinessReleaseRequest,
  type CreateBusinessRolloutRequest,
} from '@/api/businessReleases';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';

interface UploadFormValues {
  signingKeyId: string;
  reason: string;
}

interface RolloutFormValues {
  releaseUid: string;
  validationHardwareSn: string;
  targetHardwareSns?: string;
  batchSize: number;
  reason: string;
}

type ReleaseActionKind = 'verify' | 'approve' | 'suspend' | 'resume' | 'retire';

interface ReleaseActionState {
  kind: ReleaseActionKind;
  release: BusinessRelease;
}

interface CancellationTarget {
  rollout: BusinessRollout;
  deployment: BusinessDeployment;
}

const releaseColors: Record<string, string> = {
  DRAFT: 'default',
  VERIFYING: 'processing',
  VERIFICATION_FAILED: 'error',
  AWAITING_APPROVAL: 'warning',
  READY: 'success',
  SUSPENDED: 'warning',
  RETIRED: 'default',
};

const rolloutColors: Record<string, string> = {
  DRAFT: 'default',
  VALIDATING: 'processing',
  AWAITING_PROMOTION: 'success',
  VALIDATION_FAILED: 'error',
  ACTIVE: 'processing',
  COMPLETED: 'success',
  STOPPED: 'default',
};

const deploymentColors: Record<string, string> = {
  PLANNED: 'default',
  QUEUED: 'processing',
  RECEIVED: 'processing',
  DOWNLOADING: 'processing',
  VERIFYING_PACKAGE: 'processing',
  PACKAGE_READY: 'processing',
  WAITING_FOR_IDLE: 'warning',
  MIGRATING_DATA: 'warning',
  ACTIVATING: 'warning',
  VERIFYING_TARGET: 'processing',
  OBSERVING: 'processing',
  ROLLING_BACK: 'warning',
  VERIFYING_ROLLBACK: 'warning',
  SUCCEEDED: 'success',
  ROLLED_BACK: 'warning',
  DEFERRED: 'warning',
  REJECTED: 'error',
  FAILED_LOCKED: 'error',
  DOWNLOAD_AUTHORIZATION_REQUIRED: 'warning',
  CANCELLED: 'success',
};

const cancellationColors: Record<BusinessDeployment['cancellationStatus'], string> = {
  NONE: 'default',
  QUEUED: 'warning',
  CANCELLED: 'success',
  TOO_LATE: 'error',
};

function failureMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求编号：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '业务程序发布操作失败';
}

function formatBytes(value?: number): string {
  if (value === undefined) return '尚未上传';
  if (value < 1024) return `${value} 字节`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function splitHardwareSns(value?: string): string[] {
  return Array.from(new Set(
    (value || '')
      .split(/[\s,，]+/)
      .map((item) => item.trim())
      .filter(Boolean),
  ));
}

export default function BusinessReleasesPage() {
  const releaseTable = useRef<ActionType>();
  const rolloutTable = useRef<ActionType>();
  const executeCommand = useCommandExecutor();
  const [draftForm] = Form.useForm<CreateBusinessReleaseRequest>();
  const [uploadForm] = Form.useForm<UploadFormValues>();
  const [rolloutForm] = Form.useForm<RolloutFormValues>();
  const [actionForm] = Form.useForm<{ reason: string }>();
  const [readiness, setReadiness] = useState<BusinessReleaseReadiness>();
  const [readyReleases, setReadyReleases] = useState<BusinessRelease[]>([]);
  const [selectedRelease, setSelectedRelease] = useState<BusinessRelease>();
  const [selectedRollout, setSelectedRollout] = useState<BusinessRollout>();
  const [uploadTarget, setUploadTarget] = useState<BusinessRelease>();
  const [packageFile, setPackageFile] = useState<File>();
  const [signatureFile, setSignatureFile] = useState<File>();
  const [releaseAction, setReleaseAction] = useState<ReleaseActionState>();
  const [draftOpen, setDraftOpen] = useState(false);
  const [rolloutOpen, setRolloutOpen] = useState(false);
  const [stopTarget, setStopTarget] = useState<BusinessRollout>();
  const [validationTarget, setValidationTarget] = useState<BusinessRollout>();
  const [cancellationTarget, setCancellationTarget] = useState<CancellationTarget>();
  const [submitting, setSubmitting] = useState(false);

  const refreshReadiness = async () => {
    try {
      setReadiness(await getBusinessReleaseReadiness());
    } catch {
      setReadiness(undefined);
    }
  };

  useEffect(() => {
    void refreshReadiness();
  }, []);

  const refreshTables = () => {
    releaseTable.current?.reload();
    rolloutTable.current?.reload();
    void refreshReadiness();
  };

  const openReleaseDetail = async (releaseUid: string) => {
    try {
      setSelectedRelease(await getBusinessRelease(releaseUid));
    } catch (error) {
      message.error(failureMessage(error));
    }
  };

  const openRolloutDetail = async (rolloutUid: string) => {
    try {
      setSelectedRollout(await getBusinessRollout(rolloutUid));
    } catch (error) {
      message.error(failureMessage(error));
    }
  };

  useEffect(() => {
    if (selectedRollout?.status !== 'VALIDATING') return undefined;
    const timer = window.setInterval(() => {
      void openRolloutDetail(selectedRollout.rolloutUid);
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [selectedRollout?.rolloutUid, selectedRollout?.status]);

  const submitDraft = async () => {
    try {
      const values = await draftForm.validateFields();
      setSubmitting(true);
      const created = await executeCommand(
        commandKey('create-business-release', 'new', values),
        (intent) => createBusinessRelease(values, intent),
      );
      message.success('业务程序发布草稿已创建，存储路径已由后台生成');
      setDraftOpen(false);
      draftForm.resetFields();
      refreshTables();
      await openReleaseDetail(created.releaseUid);
    } catch (error) {
      if (!(error && typeof error === 'object' && 'errorFields' in error)) {
        message.error(failureMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const openUpload = (release: BusinessRelease) => {
    setUploadTarget(release);
    setPackageFile(undefined);
    setSignatureFile(undefined);
    uploadForm.resetFields();
  };

  const submitUpload = async () => {
    if (!uploadTarget) return;
    try {
      const values = await uploadForm.validateFields();
      if (!packageFile || !signatureFile) {
        message.warning('请选择业务发布包和对应的 64 字节签名文件');
        return;
      }
      setSubmitting(true);
      const keyPayload = {
        signingKeyId: values.signingKeyId,
        reason: values.reason,
        packageName: packageFile.name,
        packageSize: packageFile.size,
        packageModified: packageFile.lastModified,
        signatureName: signatureFile.name,
        signatureSize: signatureFile.size,
        signatureModified: signatureFile.lastModified,
      };
      const updated = await executeCommand(
        commandKey('upload-business-release', uploadTarget.releaseUid, keyPayload),
        (intent) => uploadBusinessReleaseArtifacts(
          uploadTarget.releaseUid,
          packageFile,
          signatureFile,
          values.signingKeyId,
          values.reason,
          intent,
        ),
      );
      message.success('签名制品已保存到业务发布专用的私有存储桶');
      setUploadTarget(undefined);
      setSelectedRelease(updated);
      refreshTables();
    } catch (error) {
      if (!(error && typeof error === 'object' && 'errorFields' in error)) {
        message.error(failureMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const actionCopy: Record<ReleaseActionKind, { title: string; warning: string }> = {
    verify: {
      title: '校验业务发布包',
      warning: '后台会从私有存储重新读回文件，核对摘要、离线签名、文件清单和三端兼容声明。',
    },
    approve: {
      title: '批准业务发布',
      warning: '批准后可以创建灰度计划；创建计划不会下发，仍需在计划详情中再次确认“开始验证设备更新”。',
    },
    suspend: {
      title: '暂停业务发布',
      warning: '暂停后不能基于该版本创建新计划，已保存的制品和审计记录不会删除。',
    },
    resume: {
      title: '恢复业务发布',
      warning: '恢复后可以再次创建灰度计划；只有管理员在计划详情中单独确认后，才会向验证设备下发。',
    },
    retire: {
      title: '归档业务发布',
      warning: '归档后不能恢复或创建新计划；历史记录仍会永久保留。',
    },
  };

  const submitReleaseAction = async () => {
    if (!releaseAction) return;
    try {
      const { reason } = await actionForm.validateFields();
      const { release, kind } = releaseAction;
      setSubmitting(true);
      const updated = await executeCommand(
        commandKey(`business-release-${kind}`, release.releaseUid, { reason }),
        (intent) => {
          if (kind === 'verify') return verifyBusinessRelease(release.releaseUid, reason, intent);
          if (kind === 'approve') return approveBusinessRelease(release.releaseUid, reason, intent);
          if (kind === 'suspend') return suspendBusinessRelease(release.releaseUid, reason, intent);
          if (kind === 'resume') return resumeBusinessRelease(release.releaseUid, reason, intent);
          return retireBusinessRelease(release.releaseUid, reason, intent);
        },
      );
      message.success(`${actionCopy[kind].title}已完成`);
      setReleaseAction(undefined);
      actionForm.resetFields();
      setSelectedRelease(updated);
      refreshTables();
    } catch (error) {
      if (!(error && typeof error === 'object' && 'errorFields' in error)) {
        message.error(failureMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const openRollout = async () => {
    try {
      const result = await listBusinessReleases({ page: 1, pageSize: 100 });
      const available = result.items.filter((release) => release.status === 'READY');
      if (available.length === 0) {
        message.warning('目前没有已经校验、批准且未暂停的业务版本');
        return;
      }
      setReadyReleases(available);
      rolloutForm.resetFields();
      rolloutForm.setFieldsValue({ batchSize: 3 });
      setRolloutOpen(true);
    } catch (error) {
      message.error(failureMessage(error));
    }
  };

  const submitRollout = async () => {
    try {
      const values = await rolloutForm.validateFields();
      const payload: CreateBusinessRolloutRequest = {
        releaseUid: values.releaseUid,
        validationHardwareSn: values.validationHardwareSn.trim(),
        targetHardwareSns: splitHardwareSns(values.targetHardwareSns),
        batchSize: values.batchSize,
        reason: values.reason,
      };
      setSubmitting(true);
      const created = await executeCommand(
        commandKey('create-business-rollout', payload.releaseUid, payload),
        (intent) => createBusinessRollout(payload, intent),
      );
      message.success('灰度计划已创建，尚未向设备发送更新命令');
      setRolloutOpen(false);
      rolloutForm.resetFields();
      refreshTables();
      await openRolloutDetail(created.rolloutUid);
    } catch (error) {
      if (!(error && typeof error === 'object' && 'errorFields' in error)) {
        message.error(failureMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const submitStop = async () => {
    if (!stopTarget) return;
    try {
      const { reason } = await actionForm.validateFields();
      setSubmitting(true);
      const updated = await executeCommand(
        commandKey('stop-business-rollout', stopTarget.rolloutUid, { reason }),
        (intent) => stopBusinessRollout(stopTarget.rolloutUid, reason, intent),
      );
      message.success('灰度计划已停止');
      setStopTarget(undefined);
      actionForm.resetFields();
      setSelectedRollout(updated);
      refreshTables();
    } catch (error) {
      if (!(error && typeof error === 'object' && 'errorFields' in error)) {
        message.error(failureMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const submitValidation = async () => {
    if (!validationTarget) return;
    try {
      const { reason } = await actionForm.validateFields();
      setSubmitting(true);
      const updated = await executeCommand(
        commandKey(
          'start-business-rollout-validation',
          validationTarget.rolloutUid,
          { reason },
        ),
        (intent) => startBusinessRolloutValidation(
          validationTarget.rolloutUid,
          reason,
          intent,
        ),
      );
      message.success(`已向验证设备 ${updated.validationHardwareSn} 提交业务程序更新`);
      setValidationTarget(undefined);
      actionForm.resetFields();
      setSelectedRollout(updated);
      refreshTables();
    } catch (error) {
      if (!(error && typeof error === 'object' && 'errorFields' in error)) {
        message.error(failureMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const submitCancellation = async () => {
    if (!cancellationTarget) return;
    try {
      const { reason } = await actionForm.validateFields();
      setSubmitting(true);
      const { rollout, deployment } = cancellationTarget;
      const updated = await executeCommand(
        commandKey(
          'cancel-business-rollout-deployment',
          deployment.deploymentUid,
          { rolloutUid: rollout.rolloutUid, reason },
        ),
        (intent) => cancelBusinessRolloutDeployment(
          rollout.rolloutUid,
          deployment.deploymentUid,
          reason,
          intent,
        ),
      );
      message.success('取消请求已发送，正在等待设备确认是否仍能安全取消');
      setCancellationTarget(undefined);
      actionForm.resetFields();
      setSelectedRollout(updated);
      refreshTables();
    } catch (error) {
      if (!(error && typeof error === 'object' && 'errorFields' in error)) {
        message.error(failureMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const releaseColumns: ProColumns<BusinessRelease>[] = [
    { title: '业务版本', dataIndex: 'versionName', width: 150 },
    {
      title: '当前状态',
      width: 180,
      render: (_, release) => (
        <Tag color={releaseColors[release.status] || 'default'}>
          {release.statusLabel}
        </Tag>
      ),
    },
    { title: '状态说明', dataIndex: 'statusDescription', ellipsis: true },
    {
      title: '发布包',
      width: 120,
      render: (_, release) => release.artifactUploaded
        ? formatBytes(release.packageSize)
        : '尚未上传',
    },
    { title: '创建人', dataIndex: 'createdBy', width: 120 },
    {
      title: '创建时间',
      width: 180,
      render: (_, release) => formatShanghaiTime(release.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 90,
      render: (_, release) => (
        <Button type="link" onClick={() => void openReleaseDetail(release.releaseUid)}>
          查看
        </Button>
      ),
    },
  ];

  const rolloutColumns: ProColumns<BusinessRollout>[] = [
    { title: '目标业务版本', render: (_, rollout) => rollout.release.versionName },
    {
      title: '计划状态',
      width: 180,
      render: (_, rollout) => (
        <Tag color={rolloutColors[rollout.status] || 'default'}>
          {rollout.statusLabel}
        </Tag>
      ),
    },
    { title: '验证设备', dataIndex: 'validationHardwareSn', width: 180 },
    {
      title: '计划规模',
      width: 160,
      render: (_, rollout) => `${rollout.maximumWaveNo} 个后续批次，每批最多 ${rollout.batchSize} 台`,
    },
    { title: '创建人', dataIndex: 'createdBy', width: 120 },
    {
      title: '创建时间',
      width: 180,
      render: (_, rollout) => formatShanghaiTime(rollout.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 90,
      render: (_, rollout) => (
        <Button type="link" onClick={() => void openRolloutDetail(rollout.rolloutUid)}>
          查看
        </Button>
      ),
    },
  ];

  const deploymentColumns = [
    { title: '设备硬件编号', dataIndex: 'hardwareSn', width: 180, fixed: 'left' as const },
    { title: '在计划中的角色', dataIndex: 'kindLabel', width: 130 },
    {
      title: '所属批次',
      render: (_: unknown, row: BusinessDeployment) => row.kind === 'VALIDATION'
        ? '单设备验证'
        : `第 ${row.waveNo} 批`,
    },
    {
      title: '当前进度',
      width: 210,
      render: (_: unknown, row: BusinessDeployment) => (
        <Tag color={deploymentColors[row.status] || 'default'}>{row.statusLabel}</Tag>
      ),
    },
    {
      title: '取消处理',
      width: 250,
      render: (_: unknown, row: BusinessDeployment) => (
        <Space direction="vertical" size={2}>
          <Tag color={cancellationColors[row.cancellationStatus]}>
            {row.cancellationStatusLabel}
          </Tag>
          {row.cancelReason && <span>原因：{row.cancelReason}</span>}
          {row.cancelResultAt && (
            <span>设备确认：{formatShanghaiTime(row.cancelResultAt)}</span>
          )}
        </Space>
      ),
    },
    { title: '新业务入口', dataIndex: 'businessAdmissionLabel', width: 260 },
    {
      title: '执行次数',
      width: 220,
      render: (_: unknown, row: BusinessDeployment) => (
        `下载 ${row.downloadAttemptCount} 次；安装 ${row.targetAttemptCount} 次；恢复 ${row.rollbackAttemptCount} 次`
      ),
    },
    {
      title: '设备当前确认的业务版本',
      width: 190,
      render: (_: unknown, row: BusinessDeployment) => row.installedVersionName || '尚未确认',
    },
    {
      title: '本地数据恢复',
      width: 130,
      render: (_: unknown, row: BusinessDeployment) => row.databaseRestored ? '已恢复更新前快照' : '未恢复快照',
    },
    {
      title: '异常说明',
      dataIndex: 'errorMessage',
      width: 300,
      render: (value: string | undefined) => value || '无',
    },
    { title: '创建计划时的检查', dataIndex: 'eligibilitySummary', width: 320 },
    {
      title: '最近更新时间',
      width: 180,
      render: (_: unknown, row: BusinessDeployment) => formatShanghaiTime(row.updatedAt),
    },
  ];

  const auditColumns = [
    { title: '操作', dataIndex: 'actionLabel' },
    { title: '操作后状态', dataIndex: 'resultingStatusLabel' },
    { title: '操作人', dataIndex: 'requestedBy' },
    { title: '原因', dataIndex: 'reason' },
    {
      title: '时间',
      render: (_: unknown, row: BusinessReleaseAction | BusinessRolloutAction) => (
        formatShanghaiTime(row.createdAt)
      ),
    },
  ];

  const openAction = (kind: ReleaseActionKind, release: BusinessRelease) => {
    actionForm.resetFields();
    setReleaseAction({ kind, release });
  };

  const validationDeployment = selectedRollout?.deployments.find(
    (deployment) => deployment.kind === 'VALIDATION',
  );

  return (
    <PageContainer
      header={pageHeader(
        '香橙派业务程序发布',
        '上传离线签名的业务程序包，按设备实际状态验证一台设备，再决定是否进入后续分批更新。',
      )}
    >
      <Alert
        showIcon
        type={readiness?.remoteDispatchEnabled ? 'warning' : 'info'}
        style={{ marginBottom: 12 }}
        message={readiness?.remoteDispatchEnabled
          ? '单设备验证下发已开放，创建计划本身仍不会更新设备'
          : '设备下发当前保持关闭，创建计划不会影响现场设备'}
        description={readiness?.remoteDispatchEnabled
          ? '只有平台管理员进入计划详情，再次确认“开始验证设备更新”后，后台才会向选定的一台设备发送命令；后续批次仍不会自动开始。'
          : '可以继续准备发布包并建立计划；开启远程下发前，页面不会提供实际更新按钮。'}
      />
      {readiness && (
        <Alert
          showIcon
          type={readiness.artifactStorageAvailable && readiness.signingKeysAvailable ? 'info' : 'error'}
          style={{ marginBottom: 16 }}
          message={readiness.artifactStorageAvailable && readiness.signingKeysAvailable
            ? '发布制品存储和验签公钥已经就绪'
            : '发布控制面尚未就绪'}
          description={(
            <Space direction="vertical" size={0}>
              <span>私有制品存储：{readiness.artifactStorageMessage}</span>
              <span>离线签名公钥：{readiness.signingKeysMessage}</span>
              <span>设备下发：{readiness.dispatchMessage}</span>
            </Space>
          )}
        />
      )}
      <Tabs
        items={[
          {
            key: 'releases',
            label: '业务版本',
            children: (
              <ProTable<BusinessRelease>
                {...proTableConfig}
                actionRef={releaseTable}
                rowKey="releaseUid"
                columns={releaseColumns}
                search={false}
                request={async (params) => {
                  const result = await listBusinessReleases({
                    page: params.current,
                    pageSize: params.pageSize,
                  });
                  return { data: result.items, total: result.total, success: true };
                }}
                toolBarRender={() => [
                  <Button key="create" type="primary" onClick={() => setDraftOpen(true)}>
                    创建业务版本草稿
                  </Button>,
                ]}
              />
            ),
          },
          {
            key: 'rollouts',
            label: '灰度计划',
            children: (
              <ProTable<BusinessRollout>
                {...proTableConfig}
                actionRef={rolloutTable}
                rowKey="rolloutUid"
                columns={rolloutColumns}
                search={false}
                request={async (params) => {
                  const result = await listBusinessRollouts({
                    page: params.current,
                    pageSize: params.pageSize,
                  });
                  return { data: result.items, total: result.total, success: true };
                }}
                toolBarRender={() => [
                  <Button key="create" type="primary" onClick={() => void openRollout()}>
                    创建灰度计划
                  </Button>,
                ]}
              />
            ),
          },
        ]}
      />

      <Modal
        title="创建业务版本草稿"
        open={draftOpen}
        confirmLoading={submitting}
        onOk={() => void submitDraft()}
        onCancel={() => setDraftOpen(false)}
      >
        <Alert
          showIcon
          type="info"
          message="发布编号和私有存储路径由后台自动生成"
          description="这里只填写发布包中声明的业务版本名称。创建后再上传发布包和离线签名文件。"
          style={{ marginBottom: 16 }}
        />
        <Form form={draftForm} layout="vertical">
          <Form.Item
            name="versionName"
            label="业务版本名称"
            rules={[
              { required: true, message: '请填写业务版本名称' },
              { pattern: /^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/, message: '请输入语义版本，例如 1.4.0 或 1.4.0-rc.1，最多 32 个字符' },
            ]}
          >
            <Input placeholder="例如 1.3.0" />
          </Form.Item>
          <Form.Item name="releaseNotes" label="发布说明">
            <Input.TextArea rows={4} maxLength={1000} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={uploadTarget ? `上传业务版本 ${uploadTarget.versionName} 的制品` : '上传业务发布制品'}
        open={Boolean(uploadTarget)}
        width={680}
        confirmLoading={submitting}
        onOk={() => void submitUpload()}
        onCancel={() => setUploadTarget(undefined)}
      >
        <Alert
          showIcon
          type="warning"
          message="上传后制品内容不可覆盖"
          description="业务包必须由构建工具生成，签名必须是对应发布包的 64 字节 Ed25519 离线签名。若网络中断，可用原文件重试。"
          style={{ marginBottom: 16 }}
        />
        <Form form={uploadForm} layout="vertical">
          <Form.Item label="业务发布包" required extra={packageFile ? `${packageFile.name}（${formatBytes(packageFile.size)}）` : '请选择 package.tar.gz'}>
            <Input type="file" accept=".gz,application/gzip" onChange={(event) => setPackageFile(event.currentTarget.files?.[0])} />
          </Form.Item>
          <Form.Item label="离线签名文件" required extra={signatureFile ? `${signatureFile.name}（${signatureFile.size} 字节）` : '请选择 package.sig'}>
            <Input type="file" accept=".sig,application/octet-stream" onChange={(event) => setSignatureFile(event.currentTarget.files?.[0])} />
          </Form.Item>
          <Form.Item name="signingKeyId" label="签名公钥名称" rules={[{ required: true, message: '请填写后台已配置的签名公钥名称' }]}>
            <Input placeholder="例如 production-2026" maxLength={64} />
          </Form.Item>
          <Form.Item name="reason" label="上传原因" rules={[{ required: true, message: '请填写上传原因' }]}>
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={releaseAction ? actionCopy[releaseAction.kind].title : '发布操作'}
        open={Boolean(releaseAction)}
        confirmLoading={submitting}
        okButtonProps={{ danger: releaseAction?.kind === 'retire' }}
        onOk={() => void submitReleaseAction()}
        onCancel={() => setReleaseAction(undefined)}
      >
        {releaseAction && (
          <Alert showIcon type="warning" message={actionCopy[releaseAction.kind].warning} style={{ marginBottom: 16 }} />
        )}
        <Form form={actionForm} layout="vertical">
          <Form.Item name="reason" label="本次操作原因" rules={[{ required: true, message: '请填写操作原因' }]}>
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="创建灰度计划"
        open={rolloutOpen}
        width={680}
        confirmLoading={submitting}
        onOk={() => void submitRollout()}
        onCancel={() => setRolloutOpen(false)}
      >
        <Alert
          showIcon
          type="info"
          message="后台会按设备当前实际安装状态逐台检查"
          description="任意一台设备不兼容，整个计划都不会创建。计划创建成功后仍不会发送消息，需要在计划详情中单独开始验证设备更新。"
          style={{ marginBottom: 16 }}
        />
        <Form form={rolloutForm} layout="vertical">
          <Form.Item name="releaseUid" label="目标业务版本" rules={[{ required: true, message: '请选择目标版本' }]}>
            <Select
              placeholder="请选择"
              options={readyReleases.map((release) => ({
                value: release.releaseUid,
                label: `${release.versionName}（${release.statusLabel}）`,
              }))}
            />
          </Form.Item>
          <Form.Item name="validationHardwareSn" label="先验证的一台设备硬件编号" rules={[{ required: true, message: '请填写验证设备硬件编号' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="targetHardwareSns" label="后续参与灰度的设备硬件编号" extra="可不填；每行一个，也可用逗号分隔。重复设备会自动去除。">
            <Input.TextArea rows={6} />
          </Form.Item>
          <Form.Item name="batchSize" label="后续每批最多设备数" rules={[{ required: true, message: '请选择每批设备数' }]}>
            <InputNumber min={1} max={100} />
          </Form.Item>
          <Form.Item name="reason" label="创建计划的原因" rules={[{ required: true, message: '请填写创建原因' }]}>
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={selectedRelease ? `业务版本 ${selectedRelease.versionName}` : '业务版本详情'}
        open={Boolean(selectedRelease)}
        width={960}
        onClose={() => setSelectedRelease(undefined)}
        extra={selectedRelease && (
          <Space wrap>
            {selectedRelease.status === 'DRAFT' && !selectedRelease.artifactUploaded && (
              <Button type="primary" onClick={() => openUpload(selectedRelease)}>上传制品</Button>
            )}
            {((selectedRelease.status === 'DRAFT' && selectedRelease.artifactUploaded)
              || selectedRelease.status === 'VERIFICATION_FAILED') && (
              <Button type="primary" onClick={() => openAction('verify', selectedRelease)}>校验发布包</Button>
            )}
            {selectedRelease.status === 'AWAITING_APPROVAL' && (
              <Button type="primary" onClick={() => openAction('approve', selectedRelease)}>批准发布</Button>
            )}
            {selectedRelease.status === 'READY' && (
              <Button onClick={() => openAction('suspend', selectedRelease)}>暂停</Button>
            )}
            {selectedRelease.status === 'SUSPENDED' && (
              <Button type="primary" onClick={() => openAction('resume', selectedRelease)}>恢复</Button>
            )}
            {['READY', 'SUSPENDED'].includes(selectedRelease.status) && (
              <Button danger onClick={() => openAction('retire', selectedRelease)}>归档</Button>
            )}
            <Button onClick={() => void openReleaseDetail(selectedRelease.releaseUid)}>刷新</Button>
          </Space>
        )}
      >
        {selectedRelease && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              showIcon
              type={selectedRelease.status === 'VERIFICATION_FAILED' ? 'error' : 'info'}
              message={selectedRelease.statusLabel}
              description={selectedRelease.verificationErrorMessage || selectedRelease.statusDescription}
            />
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="业务版本">{selectedRelease.versionName}</Descriptions.Item>
              <Descriptions.Item label="当前状态">{selectedRelease.statusLabel}</Descriptions.Item>
              <Descriptions.Item label="发布包">{formatBytes(selectedRelease.packageSize)}</Descriptions.Item>
              <Descriptions.Item label="签名公钥">{selectedRelease.signingKeyId || '尚未上传'}</Descriptions.Item>
              <Descriptions.Item label="创建人">{selectedRelease.createdBy}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{formatShanghaiTime(selectedRelease.createdAt)}</Descriptions.Item>
              <Descriptions.Item label="发布说明" span={2}>{selectedRelease.releaseNotes || '未填写'}</Descriptions.Item>
            </Descriptions>
            {selectedRelease.compatibility && (
              <>
                <Divider orientation="left">校验通过的兼容关系</Divider>
                <Descriptions bordered size="small" column={2}>
                  <Descriptions.Item label="业务包格式">第 {selectedRelease.compatibility.packageFormatVersion} 版</Descriptions.Item>
                  <Descriptions.Item label="后台命令接口">第 {selectedRelease.compatibility.backendCommandContractVersion} 版</Descriptions.Item>
                  <Descriptions.Item label="设备上报接口">第 {selectedRelease.compatibility.deviceEventContractVersion} 版</Descriptions.Item>
                  <Descriptions.Item label="通信代理接口">{selectedRelease.compatibility.communicationBusinessProtocol}</Descriptions.Item>
                  <Descriptions.Item label="设备更新器接口">{selectedRelease.compatibility.updaterBusinessProtocol}</Descriptions.Item>
                  <Descriptions.Item label="单片机串口协议">{selectedRelease.compatibility.uartProtocol}</Descriptions.Item>
                </Descriptions>
              </>
            )}
            <Divider orientation="left">操作记录</Divider>
            <Table<BusinessReleaseAction>
              rowKey={(row) => `${row.action}-${row.createdAt}`}
              size="small"
              pagination={false}
              columns={auditColumns}
              dataSource={selectedRelease.actions}
            />
          </Space>
        )}
      </Drawer>

      <Drawer
        title={selectedRollout ? `${selectedRollout.release.versionName} 灰度计划` : '灰度计划详情'}
        open={Boolean(selectedRollout)}
        width={1040}
        onClose={() => setSelectedRollout(undefined)}
        extra={selectedRollout && (
          <Space wrap>
            {selectedRollout.status === 'DRAFT' && (
              <Button
                type="primary"
                disabled={!(
                  selectedRollout.remoteDispatchEnabled
                  && readiness?.remoteDispatchEnabled
                  && readiness.artifactStorageAvailable
                  && readiness.signingKeysAvailable
                )}
                title={selectedRollout.remoteDispatchEnabled
                  ? readiness?.dispatchMessage
                  : '该计划创建时设备下发尚未开放，需要在开放后重新创建计划'}
                onClick={() => {
                  actionForm.resetFields();
                  setValidationTarget(selectedRollout);
                }}
              >
                开始验证设备更新
              </Button>
            )}
            {selectedRollout.status === 'DRAFT' && (
              <Button danger onClick={() => {
                actionForm.resetFields();
                setStopTarget(selectedRollout);
              }}>
                停止计划
              </Button>
            )}
            {selectedRollout.status === 'VALIDATING'
              && validationDeployment?.cancellationStatus === 'NONE' && (
              <Button danger onClick={() => {
                actionForm.resetFields();
                setCancellationTarget({
                  rollout: selectedRollout,
                  deployment: validationDeployment,
                });
              }}>
                请求安全取消这台设备的更新
              </Button>
            )}
            {selectedRollout.status === 'VALIDATING'
              && validationDeployment?.cancellationStatus === 'QUEUED' && (
              <Button disabled>正在等待设备确认取消结果</Button>
            )}
            <Button onClick={() => void openRolloutDetail(selectedRollout.rolloutUid)}>刷新</Button>
          </Space>
        )}
      >
        {selectedRollout && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              showIcon
              type={selectedRollout.status === 'VALIDATION_FAILED' ? 'error' : 'info'}
              message={{
                DRAFT: '计划已建立，尚未向任何设备发送更新',
                VALIDATING: validationDeployment?.cancellationStatus === 'QUEUED'
                  ? `已请求验证设备 ${selectedRollout.validationHardwareSn} 安全取消，正在等待设备确认`
                  : validationDeployment?.cancellationStatus === 'TOO_LATE'
                    ? '设备已经开始切换程序，无法安全取消，本次更新会继续'
                    : `验证设备 ${selectedRollout.validationHardwareSn} 正在执行更新`,
                AWAITING_PROMOTION: '验证设备更新成功，等待管理员决定后续批次',
                VALIDATION_FAILED: '验证设备未通过更新，后续设备没有收到命令',
                ACTIVE: '后续分批更新正在进行',
                COMPLETED: '本次灰度更新已经完成',
                STOPPED: '本计划已经停止',
              }[selectedRollout.status] || selectedRollout.statusLabel}
              description={{
                DRAFT: '只有再次确认开始验证后，后台才会向选定的一台设备下发。',
                VALIDATING: validationDeployment?.cancellationStatus === 'QUEUED'
                  ? '点击取消并不代表已经取消。后端保持业务暂停，直到设备明确回复“已安全取消”或“已经来不及取消”；页面每 5 秒刷新一次。'
                  : validationDeployment?.cancellationStatus === 'TOO_LATE'
                    ? '设备已经进入本地数据备份或程序切换阶段。为了避免损坏设备，系统不再强行中断；更新失败时仍会自动恢复数据库和上一版本。'
                    : '旧程序会在下载期间继续工作；准备切换时设备会停止接收新业务，并等待正在进行的投递或清运安全结束。页面每 5 秒刷新一次。',
                AWAITING_PROMOTION: '后台已收到更新成功证据；仍需设备新的实际安装状态确认兼容性，后续批次不会自动开始。',
                VALIDATION_FAILED: '设备会按安全状态机延后、拒绝或恢复原版本；请查看下方中文异常说明。',
                ACTIVE: '系统只更新本批设备，仍会观察失败条件并阻止自动扩大范围。',
                COMPLETED: '所有已放行设备均已收敛到终态。',
                STOPPED: '未下发设备不会更新，历史记录继续保留。',
              }[selectedRollout.status] || '请查看设备进度。'}
            />
            <Descriptions bordered size="small" column={3}>
              <Descriptions.Item label="计划状态"><Tag color={rolloutColors[selectedRollout.status] || 'default'}>{selectedRollout.statusLabel}</Tag></Descriptions.Item>
              <Descriptions.Item label="目标业务版本">{selectedRollout.release.versionName}</Descriptions.Item>
              <Descriptions.Item label="先验证的设备">{selectedRollout.validationHardwareSn}</Descriptions.Item>
              <Descriptions.Item label="后续每批最多">{selectedRollout.batchSize} 台</Descriptions.Item>
              <Descriptions.Item label="观察时间">{selectedRollout.observationWindowMinutes} 分钟</Descriptions.Item>
              <Descriptions.Item label="最多重试">{selectedRollout.maximumRetryCount} 次</Descriptions.Item>
              <Descriptions.Item label="下载最长等待">{selectedRollout.downloadTimeoutMinutes} 分钟</Descriptions.Item>
              <Descriptions.Item label="等待当前业务结束">{selectedRollout.drainTimeoutMinutes} 分钟</Descriptions.Item>
              <Descriptions.Item label="设备下发">{selectedRollout.remoteDispatchEnabled ? '该计划允许手动验证一台设备' : '该计划创建时未开放'}</Descriptions.Item>
              <Descriptions.Item label="创建原因" span={3}>{selectedRollout.reason}</Descriptions.Item>
            </Descriptions>
            <Divider orientation="left">计划中的设备</Divider>
            <Table<BusinessDeployment>
              rowKey="deploymentUid"
              size="small"
              pagination={false}
              columns={deploymentColumns}
              dataSource={selectedRollout.deployments}
              scroll={{ x: 2450 }}
            />
            <Divider orientation="left">操作记录</Divider>
            <Table<BusinessRolloutAction>
              rowKey={(row) => `${row.action}-${row.createdAt}`}
              size="small"
              pagination={false}
              columns={auditColumns}
              dataSource={selectedRollout.actions}
            />
          </Space>
        )}
      </Drawer>

      <Modal
        title="开始验证设备更新"
        open={Boolean(validationTarget)}
        confirmLoading={submitting}
        okText="确认发送到这一台设备"
        onOk={() => void submitValidation()}
        onCancel={() => setValidationTarget(undefined)}
      >
        <Alert
          showIcon
          type="warning"
          message={validationTarget
            ? `本次只会更新验证设备 ${validationTarget.validationHardwareSn}`
            : '本次只会更新一台验证设备'}
          description="后台会重新核对设备当前实际版本，再生成一条可靠更新命令。设备下载期间旧程序继续工作；切换前会停止接收新业务并等待现场业务结束。新版本异常时设备会自动恢复数据库和原业务版本，后续批次不会自动开始。"
          style={{ marginBottom: 16 }}
        />
        <Form form={actionForm} layout="vertical">
          <Form.Item
            name="reason"
            label="开始本次验证的原因"
            rules={[{ required: true, message: '请填写开始验证的原因' }]}
          >
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="请求安全取消验证设备更新"
        open={Boolean(cancellationTarget)}
        confirmLoading={submitting}
        okText="发送取消请求"
        okButtonProps={{ danger: true }}
        onOk={() => void submitCancellation()}
        onCancel={() => setCancellationTarget(undefined)}
      >
        <Alert
          showIcon
          type="warning"
          message={cancellationTarget
            ? `将请求设备 ${cancellationTarget.deployment.hardwareSn} 停止本次更新`
            : '将请求验证设备停止本次更新'}
          description="这不是强制断电或立即杀死程序。设备只会在尚未开始本地数据库备份和程序切换时取消；若已经进入切换阶段，设备会回复“已经来不及取消”并继续更新，失败时仍按原流程恢复数据库和上一版本。后端收到设备明确结果前会继续暂停新业务。"
          style={{ marginBottom: 16 }}
        />
        <Form form={actionForm} layout="vertical">
          <Form.Item
            name="reason"
            label="请求取消的原因"
            rules={[{ required: true, message: '请填写请求取消的原因' }]}
          >
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="停止灰度计划"
        open={Boolean(stopTarget)}
        confirmLoading={submitting}
        okButtonProps={{ danger: true }}
        onOk={() => void submitStop()}
        onCancel={() => setStopTarget(undefined)}
      >
        <Alert showIcon type="warning" message="停止后不能恢复；由于计划从未下发，设备不会受到影响。" style={{ marginBottom: 16 }} />
        <Form form={actionForm} layout="vertical">
          <Form.Item name="reason" label="停止原因" rules={[{ required: true, message: '请填写停止原因' }]}>
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
