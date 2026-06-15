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
import { listTenants, createTenant, updateTenant, deleteTenant } from '@/api/tenant';
import { toProTableListResult } from '@/utils/proTable';
import { pageHeader, proTableConfig, DANGER_COLOR } from '@/utils/pageStyle';
import { STATUS, toValueEnum } from '@/constants';
import type { Tenant } from '@/types';

const statusOptions = [
  { label: STATUS[1].label, value: 1 },
  { label: STATUS[0].label, value: 0 },
];

export default function TenantPage() {
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const [editing, setEditing] = useState<Tenant | null>(null);
  const [open, setOpen] = useState(false);

  const openCreate = () => {
    setEditing(null);
    setOpen(true);
  };
  const openEdit = (record: Tenant) => {
    setEditing(record);
    setOpen(true);
  };

  const handleSubmit = async (values: Tenant) => {
    if (editing?.id) {
      await updateTenant(editing.id, { ...editing, ...values });
      message.success('修改成功');
    } else {
      await createTenant(values);
      message.success('新建成功');
    }
    setOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const handleDelete = async (id: number) => {
    await deleteTenant(id);
    message.success('删除成功');
    actionRef.current?.reload();
  };

  const columns: ProColumns<Tenant>[] = [
    { title: 'ID(租户号)', dataIndex: 'id', width: 90, search: false },
    { title: '租户名称', dataIndex: 'name' },
    { title: '编码', dataIndex: 'code', search: false },
    { title: '登录用户名', dataIndex: 'username', search: false },
    { title: '小程序AppID', dataIndex: 'miniappAppid', search: false, ellipsis: true },
    { title: '联系人', dataIndex: 'contactName', search: false },
    { title: '联系电话', dataIndex: 'contactPhone', search: false },
    {
      title: '状态',
      dataIndex: 'status',
      valueEnum: toValueEnum(STATUS),
      search: false,
    },
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
          title="确认删除该租户？将连带其名下数据隔离空间。"
          onConfirm={() => record.id && handleDelete(record.id)}
        >
          <a style={{ color: DANGER_COLOR }}>删除</a>
        </Popconfirm>,
      ],
    },
  ];

  return (
    <PageContainer {...pageHeader('租户管理', '多租户账号与小程序配置')}>
      <ProTable<Tenant>
        {...proTableConfig}
        search={false}
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        request={(params) => toProTableListResult(listTenants, params)}
        scroll={{ x: 1100 }}
        toolBarRender={() => [
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建租户
          </Button>,
        ]}
      />

      <ModalForm<Tenant>
        title={editing ? '编辑租户' : '新建租户'}
        open={open}
        onOpenChange={setOpen}
        initialValues={editing ?? { status: 1 }}
        modalProps={{ destroyOnClose: true }}
        grid
        rowProps={{ gutter: 16 }}
        onFinish={handleSubmit}
      >
        <ProFormText
          colProps={{ span: 12 }}
          name="name"
          label="租户名称"
          rules={[{ required: true, message: '请输入租户名称' }]}
        />
        <ProFormText colProps={{ span: 12 }} name="code" label="租户编码" />
        <ProFormText
          colProps={{ span: 12 }}
          name="username"
          label="登录用户名"
          rules={editing ? [] : [{ required: true, message: '请输入登录用户名' }]}
        />
        <ProFormText.Password
          colProps={{ span: 12 }}
          name="password"
          label="登录密码"
          tooltip={editing ? '留空则不修改密码' : undefined}
          rules={editing ? [] : [{ required: true, message: '请输入登录密码' }]}
        />
        <ProFormText
          colProps={{ span: 12 }}
          name="miniappAppid"
          label="小程序 AppID"
          tooltip="未配置可留空（唯一）"
        />
        <ProFormText.Password
          colProps={{ span: 12 }}
          name="miniappSecret"
          label="小程序 Secret"
          tooltip="只写，后端 AES 加密存储；留空不修改"
        />
        <ProFormText colProps={{ span: 12 }} name="merchantNo" label="微信商户号" />
        <ProFormText colProps={{ span: 12 }} name="contactName" label="联系人" />
        <ProFormText colProps={{ span: 12 }} name="contactPhone" label="联系电话" />
        <ProFormText colProps={{ span: 12 }} name="address" label="地址" />
        <ProFormSelect
          colProps={{ span: 12 }}
          name="status"
          label="状态"
          options={statusOptions}
          rules={[{ required: true }]}
        />
      </ModalForm>
    </PageContainer>
  );
}
