import { useEffect, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  List,
  Modal,
  Space,
  Tag,
  Typography,
} from 'antd';
import { KeyOutlined, StopOutlined } from '@ant-design/icons';
import {
  createMaintenanceSshKey,
  listMaintenanceSshKeys,
  revokeMaintenanceSshKey,
  type MaintenanceSshKey,
} from '@/api/maintenanceAccess';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';
import HelpTip from '@/components/HelpTip';

interface KeyForm {
  label: string;
  publicKey: string;
}

interface RevokeForm {
  reason: string;
}

function errorText(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '操作失败';
}

export default function MaintenanceAccessPanel() {
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const [keyForm] = Form.useForm<KeyForm>();
  const [revokeForm] = Form.useForm<RevokeForm>();
  const [keys, setKeys] = useState<MaintenanceSshKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [revoking, setRevoking] = useState<MaintenanceSshKey>();
  const [registering, setRegistering] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      setKeys(await listMaintenanceSshKeys());
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const submitKey = async () => {
    const values = await keyForm.validateFields();
    const payload = {
      label: values.label.trim(),
      publicKey: values.publicKey.trim(),
    };
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey('maintenance-ssh-key.create', payload.publicKey, payload),
        (intent) => createMaintenanceSshKey(payload, intent),
      );
      keyForm.resetFields();
      message.success('维护公钥已登记');
      setRegistering(false);
      await reload();
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setSubmitting(false);
    }
  };

  const submitRevoke = async () => {
    if (!revoking) return;
    const values = await revokeForm.validateFields();
    const payload = {
      expectedVersion: revoking.version,
      reason: values.reason.trim(),
    };
    setSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'maintenance-ssh-key.revoke',
          revoking.maintenanceSshKeyUid,
          payload,
        ),
        (intent) => revokeMaintenanceSshKey(
          revoking.maintenanceSshKeyUid,
          payload,
          intent,
        ),
      );
      setRevoking(undefined);
      revokeForm.resetFields();
      message.success('维护公钥已撤销，新会话不能再选择它');
      await reload();
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card title='远程维护公钥' extra={<Button icon={<KeyOutlined />} onClick={() => setRegistering(true)}>登记公钥</Button>}>
      <Modal title='登记维护公钥' open={registering} footer={null} onCancel={() => !submitting && setRegistering(false)}>
      <Alert
        type='warning'
        showIcon
        message='只提交公钥，不要上传私钥'
        style={{ marginBottom: 18 }}
      />
      <Form form={keyForm} layout='vertical'>
        <Form.Item
          name='label'
          label='密钥名称'
          rules={[{ required: true, whitespace: true }]}
        >
          <Input maxLength={100} placeholder='例如：办公室电脑' />
        </Form.Item>
        <Form.Item
          name='publicKey'
          label={<>Ed25519 公钥<HelpTip label='生成维护公钥'>在本机执行 <Typography.Text code copyable>ssh-keygen -t ed25519</Typography.Text>，复制生成的 .pub 文件内容。公钥只需登记一次。</HelpTip></>}
          rules={[
            { required: true, whitespace: true },
            {
              pattern: /^ssh-ed25519 [A-Za-z0-9+/]{68}$/,
              message: '请输入不含备注的完整 ssh-ed25519 公钥',
            },
          ]}
        >
          <Input.TextArea
            autoSize={{ minRows: 2, maxRows: 4 }}
            maxLength={128}
            placeholder='ssh-ed25519 AAAA...'
          />
        </Form.Item>
        <Button
          type='primary'
          icon={<KeyOutlined />}
          loading={submitting}
          onClick={() => void submitKey()}
        >
          登记公钥
        </Button>
      </Form>
      </Modal>

      <List
        style={{ marginTop: 20 }}
        loading={loading}
        dataSource={keys}
        locale={{ emptyText: '尚未登记维护公钥' }}
        renderItem={(key) => (
          <List.Item
            actions={key.status === 'ACTIVE' ? [
              <Button
                key='revoke'
                type='link'
                danger
                icon={<StopOutlined />}
                onClick={() => {
                  revokeForm.resetFields();
                  setRevoking(key);
                }}
              >
                撤销
              </Button>,
            ] : undefined}
          >
            <List.Item.Meta
              title={(
                <Space>
                  <span>{key.label}</span>
                  <Tag color={key.status === 'ACTIVE' ? 'green' : 'default'}>
                    {key.status === 'ACTIVE' ? '可用' : '已撤销'}
                  </Tag>
                </Space>
              )}
              description={(
                <Space direction='vertical' size={2}>
                  <Typography.Text code copyable>
                    {key.fingerprintSha256}
                  </Typography.Text>
                  <Typography.Text type='secondary'>
                    登记于 {formatShanghaiTime(key.createdAt)}
                  </Typography.Text>
                </Space>
              )}
            />
          </List.Item>
        )}
      />

      <Modal
        title={`撤销维护公钥：${revoking?.label ?? ''}`}
        open={Boolean(revoking)}
        confirmLoading={submitting}
        okText='确认撤销'
        okButtonProps={{ danger: true }}
        onOk={() => void submitRevoke()}
        onCancel={() => setRevoking(undefined)}
      >
        <Form form={revokeForm} layout='vertical'>
          <Form.Item
            name='reason'
            label='撤销原因'
            rules={[{ required: true, whitespace: true }]}
          >
            <Input.TextArea maxLength={500} showCount rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
