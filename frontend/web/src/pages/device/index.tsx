import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PlusOutlined } from '@ant-design/icons';
import {
  PageContainer,
  ProTable,
  ModalForm,
  ProFormText,
  ProFormSelect,
  ProFormDigit,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { Button, Popconfirm, App } from 'antd';
import { pageDevices, createDevice, updateDevice, deleteDevice } from '@/api/device';
import { toProTableResult } from '@/utils/proTable';
import {
  DEVICE_TYPE,
  DEVICE_STATUS,
  ROLE_TENANT,
  toValueEnum,
  toOptions,
} from '@/constants';
import { useAuthStore } from '@/stores/authStore';
import type { Device } from '@/types';

export default function DevicePage() {
  const actionRef = useRef<ActionType>(null);
  const navigate = useNavigate();
  const { message } = App.useApp();
  const currentRole = useAuthStore((s) => s.role);
  const isPlatform = currentRole !== ROLE_TENANT; // 平台域可改归属(tenantId)
  const [editing, setEditing] = useState<Device | null>(null);
  const [open, setOpen] = useState(false);

  const openCreate = () => {
    setEditing(null);
    setOpen(true);
  };
  const openEdit = (record: Device) => {
    setEditing(record);
    setOpen(true);
  };

  const handleSubmit = async (values: Device) => {
    if (editing?.id) {
      await updateDevice(editing.id, { ...editing, ...values });
      message.success('修改成功');
    } else {
      await createDevice(values);
      message.success('新建成功（落入平台池，待分配）');
    }
    setOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const handleDelete = async (id: number) => {
    await deleteDevice(id);
    message.success('删除成功');
    actionRef.current?.reload();
  };

  const columns: ProColumns<Device>[] = [
    { title: 'ID', dataIndex: 'id', width: 70, search: false },
    { title: '序列号', dataIndex: 'sn' },
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'type', valueEnum: toValueEnum(DEVICE_TYPE), search: false },
    {
      title: '状态',
      dataIndex: 'status',
      valueEnum: toValueEnum(DEVICE_STATUS),
      search: false,
    },
    { title: '安装地址', dataIndex: 'address', search: false, ellipsis: true },
    {
      title: '归属租户',
      dataIndex: 'tenantId',
      search: false,
      width: 100,
      render: (_, r) => (r.tenantId === 1 ? '平台池(未分配)' : r.tenantId),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 180,
      render: (_, record) => [
        <a key="door" onClick={() => navigate(`/device/${record.id}/doors`)}>
          投口管理
        </a>,
        <a key="edit" onClick={() => openEdit(record)}>
          编辑
        </a>,
        <Popconfirm
          key="del"
          title="确认删除该设备？"
          onConfirm={() => record.id && handleDelete(record.id)}
        >
          <a style={{ color: '#ff4d4f' }}>删除</a>
        </Popconfirm>,
      ],
    },
  ];

  return (
    <PageContainer>
      <ProTable<Device>
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        request={(params) => toProTableResult(pageDevices, params)}
        search={{ labelWidth: 'auto' }}
        scroll={{ x: 1100 }}
        toolBarRender={() =>
          isPlatform
            ? [
                <Button
                  key="add"
                  type="primary"
                  icon={<PlusOutlined />}
                  onClick={openCreate}
                >
                  新建设备
                </Button>,
              ]
            : []
        }
      />

      <ModalForm<Device>
        title={editing ? '编辑设备' : '新建设备'}
        open={open}
        onOpenChange={setOpen}
        initialValues={editing ?? { type: 1, status: 0 }}
        modalProps={{ destroyOnClose: true }}
        grid
        rowProps={{ gutter: 16 }}
        onFinish={handleSubmit}
      >
        <ProFormText
          colProps={{ span: 12 }}
          name="sn"
          label="序列号"
          rules={[{ required: true, message: '请输入序列号' }]}
        />
        <ProFormText
          colProps={{ span: 12 }}
          name="name"
          label="设备名称"
          rules={[{ required: true, message: '请输入设备名称' }]}
        />
        <ProFormSelect
          colProps={{ span: 12 }}
          name="type"
          label="类型"
          options={toOptions(DEVICE_TYPE)}
        />
        <ProFormSelect
          colProps={{ span: 12 }}
          name="status"
          label="状态"
          options={toOptions(DEVICE_STATUS)}
        />
        <ProFormText colProps={{ span: 24 }} name="address" label="安装地址" />
        <ProFormDigit colProps={{ span: 12 }} name="lat" label="纬度" fieldProps={{ precision: 6 }} />
        <ProFormDigit colProps={{ span: 12 }} name="lng" label="经度" fieldProps={{ precision: 6 }} />
        {isPlatform && (
          <ProFormDigit
            colProps={{ span: 12 }}
            name="tenantId"
            label="归属租户ID"
            tooltip="改此值实现分配/收回；1=平台池(未分配)"
            min={1}
            fieldProps={{ precision: 0 }}
          />
        )}
      </ModalForm>
    </PageContainer>
  );
}
