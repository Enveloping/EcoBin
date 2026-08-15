import { useRef, useState } from 'react';
import {
  EditOutlined,
  PlusOutlined,
  QrcodeOutlined,
  StopOutlined,
} from '@ant-design/icons';
import {
  ModalForm,
  PageContainer,
  ProFormText,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  App,
  Button,
  Image,
  Modal,
  Popconfirm,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  changeFactoryOperatorStatus,
  createFactoryBindingIntent,
  createFactoryOperator,
  listFactoryOperators,
  revokeFactoryBinding,
  updateFactoryOperator,
  type FactoryBindingIntent,
  type FactoryOperator,
  type FactoryOperatorStatus,
} from '@/api/factoryOperators';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import { formatShanghaiTime } from '@/utils/decimal';

interface OperatorForm {
  operatorCode: string;
  displayName: string;
}

interface ReasonForm {
  reason: string;
}

interface BindingCode {
  operator: FactoryOperator;
  intent: FactoryBindingIntent;
}

export default function FactoryOperatorsPage() {
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<FactoryOperator | null>(null);
  const [revoking, setRevoking] = useState<FactoryOperator | null>(null);
  const [bindingCode, setBindingCode] = useState<BindingCode | null>(null);
  const [bindingLoadingUid, setBindingLoadingUid] = useState('');

  const reload = () => actionRef.current?.reload();

  const submitCreate = async (values: OperatorForm) => {
    const payload = {
      operatorCode: values.operatorCode.trim().toUpperCase(),
      displayName: values.displayName.trim(),
    };
    await executeCommand(
      commandKey('factory-operator.create', payload.operatorCode, payload),
      (intent) => createFactoryOperator(payload, intent),
    );
    message.success('厂家操作员已创建；现在可以生成一次性微信绑定码');
    setCreateOpen(false);
    reload();
    return true;
  };

  const submitEdit = async (values: Pick<OperatorForm, 'displayName'>) => {
    if (!editing) return false;
    const displayName = values.displayName.trim();
    await executeCommand(
      commandKey(
        'factory-operator.update',
        editing.factoryOperatorUid,
        { expectedVersion: editing.version, displayName },
      ),
      (intent) => updateFactoryOperator(editing, displayName, intent),
    );
    message.success('操作员姓名已更新');
    setEditing(null);
    reload();
    return true;
  };

  const toggleStatus = async (operator: FactoryOperator) => {
    const enabled = operator.status !== 'ACTIVE';
    const reason = enabled
      ? '平台管理员重新启用厂家操作员'
      : '平台管理员停用厂家操作员';
    await executeCommand(
      commandKey(
        'factory-operator.status',
        operator.factoryOperatorUid,
        { expectedVersion: operator.version, enabled, reason },
      ),
      (intent) => changeFactoryOperatorStatus(
        operator,
        enabled,
        reason,
        intent,
      ),
    );
    message.success(enabled
      ? '操作员已启用；如需使用小程序，请重新生成绑定码'
      : '操作员已停用，微信绑定和现有工厂会话已撤销');
    reload();
  };

  const generateBindingCode = async (operator: FactoryOperator) => {
    setBindingLoadingUid(operator.factoryOperatorUid);
    try {
      const intent = await createFactoryBindingIntent(operator);
      setBindingCode({ operator, intent });
    } catch (error) {
      message.error(error instanceof Error ? error.message : '绑定码生成失败');
    } finally {
      setBindingLoadingUid('');
    }
  };

  const submitRevoke = async (values: ReasonForm) => {
    if (!revoking) return false;
    const reason = values.reason.trim();
    await executeCommand(
      commandKey(
        'factory-operator.binding.revoke',
        revoking.factoryOperatorUid,
        { expectedVersion: revoking.version, reason },
      ),
      (intent) => revokeFactoryBinding(revoking, reason, intent),
    );
    message.success('微信绑定与已有工厂会话已撤销');
    setRevoking(null);
    reload();
    return true;
  };

  const columns: ProColumns<FactoryOperator>[] = [
    {
      title: '工号',
      dataIndex: 'operatorCode',
      copyable: true,
      search: false,
      width: 150,
    },
    {
      title: '工号 / 姓名',
      dataIndex: 'query',
      hideInTable: true,
      hideInSetting: true,
    },
    { title: '姓名', dataIndex: 'displayName', search: false },
    {
      title: '账号状态',
      dataIndex: 'status',
      width: 120,
      valueType: 'select',
      valueEnum: {
        ACTIVE: { text: '启用', status: 'Success' },
        DISABLED: { text: '停用', status: 'Default' },
      },
      render: (_, operator) => (
        <Tag color={operator.status === 'ACTIVE' ? 'green' : 'default'}>
          {operator.status === 'ACTIVE' ? '启用' : '停用'}
        </Tag>
      ),
    },
    {
      title: '微信绑定',
      dataIndex: 'bindingStatus',
      width: 150,
      search: false,
      render: (_, operator) => operator.bindingStatus === 'ACTIVE' ? (
        <Space direction='vertical' size={0}>
          <Tag color='blue'>已绑定</Tag>
          {operator.boundAt && (
            <Typography.Text type='secondary' style={{ fontSize: 12 }}>
              {formatShanghaiTime(operator.boundAt)}
            </Typography.Text>
          )}
        </Space>
      ) : <Tag>未绑定</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      valueType: 'dateTime',
      search: false,
      width: 180,
    },
    {
      title: '操作',
      key: 'operation',
      valueType: 'option',
      width: 330,
      hideInSetting: true,
      render: (_, operator) => [
        <a key='edit' onClick={() => setEditing(operator)}>
          <EditOutlined /> 修改姓名
        </a>,
        operator.status === 'ACTIVE' && operator.bindingStatus === 'UNBOUND' && (
          <a
            key='bind'
            onClick={() => void generateBindingCode(operator)}
          >
            <QrcodeOutlined />
            {bindingLoadingUid === operator.factoryOperatorUid
              ? ' 生成中…'
              : ' 生成绑定码'}
          </a>
        ),
        operator.bindingStatus === 'ACTIVE' && (
          <a key='revoke' onClick={() => setRevoking(operator)}>
            解除绑定
          </a>
        ),
        <Popconfirm
          key='status'
          title={operator.status === 'ACTIVE'
            ? '停用后会立即解除微信绑定并撤销工厂会话，确认继续？'
            : '启用后仍需生成新的绑定码，确认继续？'}
          onConfirm={() => toggleStatus(operator)}
        >
          <a>{operator.status === 'ACTIVE' ? '停用' : '启用'}</a>
        </Popconfirm>,
      ].filter(Boolean),
    },
  ];

  return (
    <PageContainer
      {...pageHeader(
        '厂家操作员',
        '平台管理员先建工号，再把一次性微信小程序码交给对应厂家人员扫码；厂家身份不具备 Web 管理权限。',
      )}
    >
      <Alert
        showIcon
        type='info'
        style={{ marginBottom: 16 }}
        message='绑定码只用于确认“哪个微信属于哪个厂家操作员”'
        description='码有效期为 5 分钟且只能使用一次。停用操作员会同时解除绑定；重新启用后必须生成新码。'
      />
      <ProTable<FactoryOperator>
        {...proTableConfig}
        actionRef={actionRef}
        rowKey='factoryOperatorUid'
        columns={columns}
        columnsState={{
          persistenceKey: 'ecobin.web.columns.factory-operators.v1',
          persistenceType: 'localStorage',
        }}
        request={async (params) => {
          try {
            const page = await listFactoryOperators({
              page: params.current,
              pageSize: params.pageSize,
              status: params.status as FactoryOperatorStatus | undefined,
              query: params.query as string | undefined,
            });
            return { data: page.items, total: page.total, success: true };
          } catch {
            return { data: [], total: 0, success: false };
          }
        }}
        toolBarRender={() => [
          <Button
            key='create'
            type='primary'
            icon={<PlusOutlined />}
            onClick={() => setCreateOpen(true)}
          >
            新建厂家操作员
          </Button>,
        ]}
      />

      <ModalForm<OperatorForm>
        title='新建厂家操作员'
        open={createOpen}
        onOpenChange={setCreateOpen}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitCreate}
      >
        <ProFormText
          name='operatorCode'
          label='工号'
          extra='创建后不可修改；字母会自动转为大写。'
          rules={[
            { required: true },
            {
              min: 2,
              max: 64,
              pattern: /^[A-Za-z0-9][A-Za-z0-9_-]*$/,
              message: '请输入 2–64 位字母、数字、下划线或连字符',
            },
          ]}
        />
        <ProFormText
          name='displayName'
          label='姓名'
          rules={[{ required: true }, { max: 100 }]}
        />
      </ModalForm>

      <ModalForm<Pick<OperatorForm, 'displayName'>>
        title={`修改姓名 · ${editing?.operatorCode ?? ''}`}
        open={!!editing}
        initialValues={{ displayName: editing?.displayName }}
        onOpenChange={(open) => !open && setEditing(null)}
        modalProps={{ destroyOnClose: true }}
        onFinish={submitEdit}
      >
        <ProFormText
          name='displayName'
          label='姓名'
          rules={[{ required: true }, { max: 100 }]}
        />
      </ModalForm>

      <ModalForm<ReasonForm>
        title={`解除微信绑定 · ${revoking?.operatorCode ?? ''}`}
        open={!!revoking}
        onOpenChange={(open) => !open && setRevoking(null)}
        modalProps={{ destroyOnClose: true }}
        submitter={{
          searchConfig: { submitText: '确认解除' },
          submitButtonProps: { danger: true, icon: <StopOutlined /> },
        }}
        onFinish={submitRevoke}
      >
        <Alert
          showIcon
          type='warning'
          message='解除后，该微信已有的厂家会话会立即失效。'
          style={{ marginBottom: 16 }}
        />
        <ProFormText
          name='reason'
          label='解除原因'
          rules={[{ required: true }, { max: 500 }]}
        />
      </ModalForm>

      <Modal
        title={`微信绑定码 · ${bindingCode?.operator.operatorCode ?? ''}`}
        open={!!bindingCode}
        footer={null}
        destroyOnClose
        onCancel={() => setBindingCode(null)}
      >
        {bindingCode && (
          <Space direction='vertical' align='center' style={{ width: '100%' }}>
            <Image
              preview={false}
              width={300}
              src={bindingCode.intent.miniProgramCodeDataUrl}
              alt='厂家操作员微信绑定小程序码'
            />
            <Typography.Text>
              有效期至 {formatShanghaiTime(bindingCode.intent.expiresAt)}
            </Typography.Text>
            <Typography.Text type='secondary'>
              请让本人用微信扫描；不要转发或留存截图。
            </Typography.Text>
          </Space>
        )}
      </Modal>
    </PageContainer>
  );
}
