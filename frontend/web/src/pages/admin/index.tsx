import { useRef, useState } from 'react';
import { PlusOutlined } from '@ant-design/icons';
import {
  PageContainer,
  ProTable,
  ModalForm,
  ProFormText,
  ProFormSelect,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { Button, Popconfirm, App } from 'antd';
import { listAdmins, createAdmin, updateAdmin, deleteAdmin } from '@/api/admin';
import { toProTableListResult } from '@/utils/proTable';
import { pageHeader, proTableConfig, DANGER_COLOR } from '@/utils/pageStyle';
import { ROLE, STATUS, toValueEnum } from '@/constants';
import type { Admin } from '@/types';

const roleOptions = [
  { label: ROLE[9].label, value: 9 },
  { label: ROLE[8].label, value: 8 },
];
const statusOptions = [
  { label: STATUS[1].label, value: 1 },
  { label: STATUS[0].label, value: 0 },
];

export default function AdminPage() {
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const [editing, setEditing] = useState<Admin | null>(null);
  const [open, setOpen] = useState(false);

  const openCreate = () => {
    setEditing(null);
    setOpen(true);
  };
  const openEdit = (record: Admin) => {
    setEditing(record);
    setOpen(true);
  };

  const handleSubmit = async (values: Admin) => {
    if (editing?.id) {
      await updateAdmin(editing.id, { ...editing, ...values });
      message.success('修改成功');
    } else {
      await createAdmin(values);
      message.success('新建成功');
    }
    setOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const handleDelete = async (id: number) => {
    await deleteAdmin(id);
    message.success('删除成功');
    actionRef.current?.reload();
  };

  const columns: ProColumns<Admin>[] = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: '用户名', dataIndex: 'username' },
    { title: '真实姓名', dataIndex: 'realName' },
    {
      title: '角色',
      dataIndex: 'role',
      valueEnum: toValueEnum(ROLE),
      search: false,
    },
    {
      title: '状态',
      dataIndex: 'status',
      valueEnum: toValueEnum(STATUS),
      search: false,
    },
    { title: '创建时间', dataIndex: 'createTime', valueType: 'dateTime', search: false },
    {
      title: '操作',
      valueType: 'option',
      width: 140,
      render: (_, record) => [
        <a key="edit" onClick={() => openEdit(record)}>
          编辑
        </a>,
        <Popconfirm
          key="del"
          title="确认删除该管理员？"
          onConfirm={() => record.id && handleDelete(record.id)}
        >
          <a style={{ color: DANGER_COLOR }}>删除</a>
        </Popconfirm>,
      ],
    },
  ];

  return (
    <PageContainer {...pageHeader('管理员管理', '平台管理员账号维护')}>
      <ProTable<Admin>
        {...proTableConfig}
        search={false}
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        request={(params) => toProTableListResult(listAdmins, params)}
        toolBarRender={() => [
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建管理员
          </Button>,
        ]}
      />

      <ModalForm<Admin>
        title={editing ? '编辑管理员' : '新建管理员'}
        open={open}
        onOpenChange={setOpen}
        initialValues={editing ?? { role: 8, status: 1 }}
        modalProps={{ destroyOnClose: true }}
        onFinish={handleSubmit}
      >
        <ProFormText
          name="username"
          label="用户名"
          rules={[{ required: true, message: '请输入用户名' }]}
        />
        <ProFormText.Password
          name="password"
          label="密码"
          tooltip={editing ? '留空则不修改密码' : undefined}
          rules={editing ? [] : [{ required: true, message: '请输入密码' }]}
        />
        <ProFormText name="realName" label="真实姓名" />
        <ProFormSelect
          name="role"
          label="角色"
          options={roleOptions}
          rules={[{ required: true, message: '请选择角色' }]}
        />
        <ProFormSelect
          name="status"
          label="状态"
          options={statusOptions}
          rules={[{ required: true }]}
        />
      </ModalForm>
    </PageContainer>
  );
}
