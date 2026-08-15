import { useEffect, useMemo, useState } from 'react';
import {
  Alert, App, Button, Card, Descriptions, Form, Input, InputNumber,
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
  PREPARING: { label: '正在准备服务器租约', color: 'processing' },
  CONNECTING: { label: '等待设备建立隧道', color: 'processing' },
  OPEN: { label: '已开放', color: 'success' },
  CLOSING: { label: '正在关闭', color: 'warning' },
  CLOSED: { label: '已关闭', color: 'default' },
  FAILED: { label: '建立失败', color: 'error' },
  EXPIRED: { label: '已到期', color: 'default' },
};

function storageKey(hardwareSn: string): string {
  return `ecobin.remote-support-session.${hardwareSn}`;
}

function errorText(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '远程维护操作失败';
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

  const activeKeys = useMemo(
    () => keys.filter((key) => key.status === 'ACTIVE'),
    [keys],
  );

  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      setLoading(true);
      try {
        const loadedKeys = await listMaintenanceSshKeys();
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
          if (terminalStates.has(restored.state)) {
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
        if (!cancelled) message.error(errorText(error));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void restore();
    return () => { cancelled = true; };
  }, [hardwareSn]);

  useEffect(() => {
    if (!session || terminalStates.has(session.state)) return undefined;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void getRemoteSupportSession(session.sessionUid)
        .then((updated) => {
          if (cancelled) return;
          setSession(updated);
          if (terminalStates.has(updated.state)) {
            sessionStorage.removeItem(storageKey(hardwareSn));
          }
        })
        .catch(() => undefined);
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [hardwareSn, session?.sessionUid, session?.state]);

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
      message.success('开启指令已交给 OneNet，正在等待香橙派建立隧道');
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
      message.success('关闭指令已提交，服务器租约已开始撤销');
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setSubmitting(false);
    }
  };

  const active = session && !terminalStates.has(session.state);

  return (
    <Card
      loading={loading}
      title={<Space><ToolOutlined />临时远程维护</Space>}
      extra={!active ? (
        <Button
          type="primary"
          icon={<CodeOutlined />}
          disabled={!activeKeys.length}
          onClick={() => {
            openForm.setFieldsValue({
              maintenanceSshKeyUid: activeKeys[0]?.maintenanceSshKeyUid,
              lifetimeSeconds: 900,
            });
            setOpenModal(true);
          }}
        >开启反向 SSH</Button>
      ) : (
        <Button
          danger
          icon={<DisconnectOutlined />}
          onClick={() => {
            closeForm.resetFields();
            setCloseModal(true);
          }}
        >立即关闭</Button>
      )}
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
            {session.failureCode && (
              <Descriptions.Item label="失败代码" span={2}>
                <Typography.Text type="danger" code>
                  {session.failureCode}
                </Typography.Text>
              </Descriptions.Item>
            )}
          </Descriptions>
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

      <Modal
        title={`开启 ${hardwareSn} 的临时反向 SSH`}
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
          description="22011～22014 是共享端口池；关闭或到期后端口会被其他设备复用。会话最长 30 分钟，服务器和设备两端都会执行到期关闭。"
          style={{ marginBottom: 18 }}
        />
        <Form form={openForm} layout="vertical">
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
        <Form form={closeForm} layout="vertical">
          <Form.Item
            name="reason"
            label="关闭原因"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
