import { useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { PlusOutlined, ArrowLeftOutlined } from '@ant-design/icons';
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
import { listDoors, createDoor, updateDoor, deleteDoor } from '@/api/door';
import { pageHeader, proTableConfig, DANGER_COLOR } from '@/utils/pageStyle';
import {
  WASTE_TYPE1,
  WASTE_TYPE2,
  ENABLED,
  toValueEnum,
  toOptions,
} from '@/constants';
import type { Door } from '@/types';

export default function DoorPage() {
  const { deviceId } = useParams();
  const did = Number(deviceId);
  const navigate = useNavigate();
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const [editing, setEditing] = useState<Door | null>(null);
  const [open, setOpen] = useState(false);

  const openCreate = () => {
    setEditing(null);
    setOpen(true);
  };
  const openEdit = (record: Door) => {
    setEditing(record);
    setOpen(true);
  };

  const handleSubmit = async (values: Door) => {
    const payload = { ...values, deviceId: did };
    if (editing?.id) {
      await updateDoor(editing.id, { ...editing, ...payload });
      message.success('修改成功');
    } else {
      await createDoor(payload);
      message.success('新建成功');
    }
    setOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const handleDelete = async (id: number) => {
    await deleteDoor(id);
    message.success('删除成功');
    actionRef.current?.reload();
  };

  const columns: ProColumns<Door>[] = [
    { title: '投口号', dataIndex: 'doorIndex', width: 80 },
    { title: '名称', dataIndex: 'name' },
    { title: '一级分类', dataIndex: 'wasteType1', valueEnum: toValueEnum(WASTE_TYPE1) },
    { title: '二级分类', dataIndex: 'wasteType2', valueEnum: toValueEnum(WASTE_TYPE2) },
    { title: '单价(元/kg)', dataIndex: 'price', valueType: 'money' },
    { title: '启用', dataIndex: 'enabled', valueEnum: toValueEnum(ENABLED) },
    { title: '排序', dataIndex: 'sortOrder', width: 70 },
    {
      title: '操作',
      valueType: 'option',
      width: 120,
      render: (_, record) => [
        <a key="edit" onClick={() => openEdit(record)}>
          编辑
        </a>,
        <Popconfirm
          key="del"
          title="确认删除该投口？"
          onConfirm={() => record.id && handleDelete(record.id)}
        >
          <a style={{ color: DANGER_COLOR }}>删除</a>
        </Popconfirm>,
      ],
    },
  ];

  return (
    <PageContainer
      {...pageHeader(`设备 #${did} 的投口管理`)}
      extra={
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/device')}>
          返回设备列表
        </Button>
      }
    >
      <ProTable<Door>
        {...proTableConfig}
        search={false}
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        pagination={false}
        request={async () => {
          try {
            const list = await listDoors(did);
            return { data: list ?? [], success: true };
          } catch {
            return { data: [], success: false };
          }
        }}
        toolBarRender={() => [
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建投口
          </Button>,
        ]}
      />

      <ModalForm<Door>
        title={editing ? '编辑投口' : '新建投口'}
        open={open}
        onOpenChange={setOpen}
        initialValues={editing ?? { wasteType1: 2, wasteType2: 0, enabled: 1, sortOrder: 0 }}
        modalProps={{ destroyOnClose: true }}
        grid
        rowProps={{ gutter: 16 }}
        onFinish={handleSubmit}
      >
        <ProFormDigit
          colProps={{ span: 12 }}
          name="doorIndex"
          label="投口号(1-6)"
          min={1}
          max={6}
          fieldProps={{ precision: 0 }}
          rules={[{ required: true, message: '请输入投口号' }]}
        />
        <ProFormText colProps={{ span: 12 }} name="name" label="投口名称" />
        <ProFormSelect
          colProps={{ span: 12 }}
          name="wasteType1"
          label="一级分类"
          options={toOptions(WASTE_TYPE1)}
          rules={[{ required: true, message: '请选择一级分类' }]}
        />
        <ProFormSelect
          colProps={{ span: 12 }}
          name="wasteType2"
          label="二级分类"
          options={toOptions(WASTE_TYPE2)}
        />
        <ProFormDigit
          colProps={{ span: 12 }}
          name="price"
          label="单价(元/kg)"
          min={0}
          fieldProps={{ precision: 2 }}
        />
        <ProFormDigit
          colProps={{ span: 12 }}
          name="sortOrder"
          label="排序"
          fieldProps={{ precision: 0 }}
        />
        <ProFormSelect
          colProps={{ span: 12 }}
          name="enabled"
          label="启用"
          options={toOptions(ENABLED)}
        />
      </ModalForm>
    </PageContainer>
  );
}
