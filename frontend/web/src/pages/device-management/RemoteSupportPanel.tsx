import { useEffect, useMemo, useState } from 'react';
import {
  Alert, App, Button, Card, Collapse, Descriptions, Form, Input, InputNumber,
  Modal, Select, Space, Tag, Typography,
} from 'antd';
import { CodeOutlined, DisconnectOutlined, ToolOutlined } from '@ant-design/icons';
import {
  listMaintenanceSshKeys,
  type MaintenanceSshKey,
} from '@/api/maintenanceAccess';
import {
  closeRemoteSupportSession,
  getCurrentRemoteSupportSession,
  getRemoteSupportSession,
  openRemoteSupportSession,
  type RemoteSupportSession,
  type RemoteSupportState,
} from '@/api/remoteSupport';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';
import { operatorErrorMessage } from './operatorErrorPresentation';

interface OpenForm {
  maintenanceSshKeyUid: string;
  lifetimeSeconds: number;
  reason: string;
}

interface CloseForm { reason: string }

const terminalStates = new Set<RemoteSupportState>([
  'CLOSED', 'FAILED', 'EXPIRED',
]);

const stateCopy: Record<RemoteSupportState, { label: string; color: string }> = {
  PREPARING: { label: '正在准备连接资源', color: 'processing' },
  CONNECTING: { label: '系统正在安排设备连接', color: 'processing' },
  OPEN: { label: '已开放', color: 'success' },
  RECONNECTING: { label: '设备暂时离线，等待重连', color: 'warning' },
  CLOSING: { label: '正在关闭', color: 'warning' },
  CLOSED: { label: '已关闭', color: 'default' },
  FAILED: { label: '建立失败', color: 'error' },
  EXPIRED: { label: '已到期', color: 'default' },
};

const failureCopy: Record<string, { title: string; action: string }> = {
  CREDENTIALS_INVALID: {
    title: '设备的远程维护凭证无效',
    action: '请重新安装与当前设备匹配的维护凭证，再发起新的远程维护会话。',
  },
  SSH_NOT_AVAILABLE: {
    title: '设备缺少远程维护程序',
    action: '请确认设备已安装包含远程维护能力的正式软件版本。',
  },
  SSH_START_FAILED: {
    title: '设备未能启动安全连接',
    action: '请确认设备在线且网络正常；若再次失败，请联系技术人员检查设备服务。',
  },
  SSH_EXITED: {
    title: '远程维护连接意外中断',
    action: '请先确认设备仍在线；如现场网络已经恢复，可重新发起会话。',
  },
  PROCESS_SUPERVISION_FAILED: {
    title: '设备未能持续监控远程连接',
    action: '请联系技术人员检查设备的远程维护服务，再重新发起会话。',
  },
  CONNECT_TIMEOUT: {
    title: '设备未在规定时间内建立连接',
    action: '请确认设备在线且蜂窝网络稳定，然后重新发起会话。',
  },
  SERVER_LEASE_CONFLICT: {
    title: '服务器连接端口被其他会话占用',
    action: '请先关闭冲突的远程维护会话；端口释放后再重新发起。',
  },
};

function remoteSupportFailureCopy(code: string) {
  return failureCopy[code] || {
    title: '远程维护连接未能建立',
    action: '请确认设备在线后重新发起；若仍失败，请展开技术诊断并联系技术人员。',
  };
}

function storageKey(hardwareSn: string): string {
  return `ecobin.remote-support-session.${hardwareSn}`;
}

function errorText(error: unknown): string {
  return operatorErrorMessage(error, '远程维护操作未完成，请稍后再试');
}

export default function RemoteSupportPanel({ hardwareSn }: {
  hardwareSn: string;
}) {
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const [openForm] = Form.useForm<OpenForm>();
  const [closeForm] = Form.useForm<CloseForm>();
  const [keys, setKeys] = useState<MaintenanceSshKey[]>([]);
  const [session, setSession] = useState<RemoteSupportSession>();
  const [openModal, setOpenModal] = useState(false);
  const [closeModal, setCloseModal] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState<string>();
  const [refreshVersion, setRefreshVersion] = useState(0);

  const activeKeys = useMemo(
    () => keys.filter((key) => key.status === 'ACTIVE'),
    [keys],
  );

  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      setLoading(true);
      setReadError(undefined);
      try {
        const loadedKeys = await listMaintenanceSshKeys({ silent: true });
        if (cancelled) return;
        setKeys(loadedKeys);
        try {
          const current = await getCurrentRemoteSupportSession(hardwareSn);
          if (cancelled) return;
          sessionStorage.setItem(storageKey(hardwareSn), current.sessionUid);
          setSession(current);
          return;
        } catch (error) {
          if (!(error instanceof ApiProblem && error.status === 404)) {
            throw error;
          }
        }
        const sessionUid = sessionStorage.getItem(storageKey(hardwareSn));
        if (!sessionUid) return;
        try {
          const restored = await getRemoteSupportSession(sessionUid);
          if (cancelled) return;
          setSession(restored);
          if (terminalStates.has(restored.state)
            && !restored.leaseCleanupPending) {
            sessionStorage.removeItem(storageKey(hardwareSn));
          }
        } catch (error) {
          if (error instanceof ApiProblem && error.status === 404) {
            sessionStorage.removeItem(storageKey(hardwareSn));
          } else {
            throw error;
          }
        }
      } catch (error) {
        if (!cancelled) setReadError(errorText(error));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void restore();
    return () => { cancelled = true; };
  }, [hardwareSn, refreshVersion]);

  useEffect(() => {
    if (!session || (terminalStates.has(session.state)
      && !session.leaseCleanupPending)) return undefined;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void getRemoteSupportSession(session.sessionUid)
        .then((updated) => {
          if (cancelled) return;
          setReadError(undefined);
          setSession(updated);
          if (terminalStates.has(updated.state)
            && !updated.leaseCleanupPending) {
            sessionStorage.removeItem(storageKey(hardwareSn));
          }
        })
        .catch((error) => {
          if (!cancelled) setReadError(errorText(error));
        });
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    hardwareSn,
    session?.sessionUid,
    session?.state,
    session?.leaseCleanupPending,
  ]);

  const open = async () => {
    const values = await openForm.validateFields();
    const payload = {
      maintenanceSshKeyUid: values.maintenanceSshKeyUid,
      lifetimeSeconds: values.lifetimeSeconds,
      reason: values.reason.trim(),
    };
    setSubmitting(true);
    try {
      const created = await executeCommand(
        commandKey('remote-support.open', hardwareSn, payload),
        (intent) => openRemoteSupportSession(hardwareSn, payload, intent),
      );
      sessionStorage.setItem(storageKey(hardwareSn), created.sessionUid);
      setSession(created);
      setOpenModal(false);
      message.success('远程协助请求已登记，系统正在安排发送给设备');
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setSubmitting(false);
    }
  };

  const close = async () => {
    if (!session) return;
    const values = await closeForm.validateFields();
    const payload = { reason: values.reason.trim() };
    setSubmitting(true);
    try {
      const updated = await executeCommand(
        commandKey('remote-support.close', session.sessionUid, payload),
        (intent) => closeRemoteSupportSession(
          session.sessionUid, payload, intent,
        ),
      );
      setSession(updated);
      setCloseModal(false);
      message.success('关闭请求已登记，系统正在安全收回远程连接');
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setSubmitting(false);
    }
  };

  const canClose = session && !terminalStates.has(session.state);
  const reservesPort = session
    && (canClose || session.leaseCleanupPending);
  const failure = session?.failureCode
    ? remoteSupportFailureCopy(session.failureCode)
    : undefined;

  return (
    <section aria-label="远程维护">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {readError && (
          <Alert type="warning" showIcon
            message={session ? '远程维护状态更新失败，显示上次结果' : '远程维护状态读取失败'}
            description={readError}
            action={<Button size="small" onClick={() => setRefreshVersion(value => value + 1)}>重试</Button>}
          />
        )}
        {reservesPort && session && (
          <Card size="small" title="临时远程维护" extra={canClose ? (<Button
            danger
            icon={<DisconnectOutlined />}
            onClick={() => {
              closeForm.resetFields();
              setCloseModal(true);
            }}
          >立即关闭</Button>) : null}>
            <Space wrap>
              <Tag color={stateCopy[session.state].color}>{stateCopy[session.state].label}</Tag>
              <Typography.Text>到期：{formatShanghaiTime(session.expiresAt)}</Typography.Text>
            </Space>
          </Card>
        )}
        {session?.leaseCleanupPending && (
          <Alert
            type="warning"
            showIcon
            message="会话已结束，服务器正在确认远程入口已清除"
            description="确认完成前该共享端口不会交给其他设备，也不能在本设备上开启新会话。页面会自动刷新。"
          />
        )}
        {failure && (
          <Alert
            type="error"
            showIcon
            message={failure.title}
            description={failure.action}
          />
        )}
        <Collapse size="small" items={[{
          key: 'remote-support-details',
          label: reservesPort ? '连接与维护详情' : loading ? '远程维护 · 读取中' : '远程维护',
          children: (
            <Card
              loading={loading}
              size="small"
              title={<Space><ToolOutlined />维护详情</Space>}
              extra={!reservesPort ? (
                <Button
                  type="primary"
                  icon={<CodeOutlined />}
                  disabled={loading || Boolean(readError) || !activeKeys.length}
                  onClick={() => {
                    openForm.setFieldsValue({
                      maintenanceSshKeyUid: activeKeys[0]?.maintenanceSshKeyUid,
                      lifetimeSeconds: 900,
                    });
                    setOpenModal(true);
                  }}
                >开启远程维护</Button>
              ) : null}
            >
              {!activeKeys.length && (
                <Alert
                  type="warning"
                  showIcon
                  message="请先在“账号设置”登记本机 SSH 公钥"
                  description="公钥按管理员登记一次，不需要逐台设备配置。私钥始终留在你的电脑上。"
                />
              )}
              {session && (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Descriptions size="small" bordered column={2}>
                    <Descriptions.Item label="会话状态">
                      <Tag color={stateCopy[session.state].color}>
                        {stateCopy[session.state].label}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="复用端口">
                      {session.remotePort}
                    </Descriptions.Item>
                    <Descriptions.Item label="到期时间" span={2}>
                      {formatShanghaiTime(session.expiresAt)}
                    </Descriptions.Item>
                  </Descriptions>
                  {session.failureCode && (
                    <Collapse
                      ghost
                      size="small"
                      items={[{
                        key: 'remote-support-failure-diagnostic',
                        label: '技术诊断（报修时使用）',
                        children: (
                          <Descriptions size="small" column={1}>
                            <Descriptions.Item label="失败代码">
                              <Typography.Text type="danger" copyable code>
                                {session.failureCode}
                              </Typography.Text>
                            </Descriptions.Item>
                          </Descriptions>
                        ),
                      }]}
                    />
                  )}
                  {session.state === 'OPEN' && session.certificate && (
                    <Alert
                      type="success"
                      showIcon
                      message="隧道与临时登录证书均已就绪"
                      description={(
                        <Space direction="vertical" size={8} style={{ width: '100%' }}>
                          <Typography.Text>
                            将证书保存为私钥同名的
                            <Typography.Text code>-cert.pub</Typography.Text>
                            文件。例如私钥为
                            <Typography.Text code>id_ed25519</Typography.Text>
                            ，证书应为
                            <Typography.Text code>id_ed25519-cert.pub</Typography.Text>。
                          </Typography.Text>
                          <Typography.Text strong>临时证书：</Typography.Text>
                          <Typography.Paragraph code copyable>
                            {session.certificate}
                          </Typography.Paragraph>
                          <Typography.Text strong>设备主机密钥：</Typography.Text>
                          <Typography.Paragraph code copyable>
                            {session.knownHostsLine}
                          </Typography.Paragraph>
                          <Typography.Text strong>连接命令：</Typography.Text>
                          <Typography.Paragraph code copyable>
                            {session.sshCommand}
                          </Typography.Paragraph>
                        </Space>
                      )}
                    />
                  )}
                </Space>
              )}

            </Card>
          ),
        }]} />
      </Space>
      <Modal
        title={`开启 ${hardwareSn} 的临时远程维护`}
        open={openModal}
        confirmLoading={submitting}
        okText="确认开启"
        onOk={() => void open()}
        onCancel={() => setOpenModal(false)}
      >
        <Alert
          type="warning"
          showIcon
          message="同时最多开放 4 台设备"
          description="本次远程连接最长 30 分钟，到期自动关闭。"
          style={{ marginBottom: 18 }}
        />
        <Form form={openForm} name="open-remote-support" layout="vertical">
          <Form.Item
            name="maintenanceSshKeyUid"
            label="本机维护公钥"
            rules={[{ required: true }]}
          >
            <Select options={activeKeys.map((key) => ({
              value: key.maintenanceSshKeyUid,
              label: `${key.label} · ${key.fingerprintSha256}`,
            }))} />
          </Form.Item>
          <Form.Item
            name="lifetimeSeconds"
            label="有效期（秒）"
            rules={[{ required: true }]}
          >
            <InputNumber min={300} max={1800} step={300} />
          </Form.Item>
          <Form.Item
            name="reason"
            label="维护原因"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="关闭临时远程维护"
        open={closeModal}
        confirmLoading={submitting}
        okText="立即关闭"
        okButtonProps={{ danger: true }}
        onOk={() => void close()}
        onCancel={() => setCloseModal(false)}
      >
        <Form form={closeForm} name="close-remote-support" layout="vertical">
          <Form.Item
            name="reason"
            label="关闭原因"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  );
}
