import { useRef, useState } from 'react';
import {
  PageContainer,
  ProTable,
  ModalForm,
  ProFormSelect,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { App } from 'antd';
import { pageUsers, updateUserRole } from '@/api/user';
import { toProTableResult } from '@/utils/proTable';
import { ROLE, STATUS, ROLE_TENANT, toValueEnum } from '@/constants';
import { useAuthStore } from '@/stores/authStore';
import type { User } from '@/types';

const editableRoleOptions = [
  { label: ROLE[1].label, value: 1 },
  { label: ROLE[2].label, value: 2 },
  { label: ROLE[3].label, value: 3 },
];

export default function UserPage() {
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const currentRole = useAuthStore((s) => s.role);
  const canEditRole = currentRole === ROLE_TENANT; // 仅租户可改角色
  const [editing, setEditing] = useState<User | null>(null);
  const [open, setOpen] = useState(false);

  const handleSubmit = async (values: { role: number }) => {
    if (!editing) return false;
    await updateUserRole(editing.id, values.role);
    message.success('角色已更新，该用户旧 Token 已失效');
    setOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const columns: ProColumns<User>[] = [
    { title: 'ID', dataIndex: 'id', width: 70, search: false },
    { title: '真实姓名', dataIndex: 'realName', search: false },
    { title: '手机号', dataIndex: 'phone', search: false },
    { title: '昵称', dataIndex: 'nickname', search: false },
    { title: '角色', dataIndex: 'role', valueEnum: toValueEnum(ROLE), search: false },
    { title: '状态', dataIndex: 'status', valueEnum: toValueEnum(STATUS), search: false },
    {
      title: '可用余额(元)',
      dataIndex: 'balance',
      search: false,
      valueType: 'money',
    },
    {
      title: '冻结余额(元)',
      dataIndex: 'pendingBalance',
      search: false,
      valueType: 'money',
    },
    { title: '注册时间', dataIndex: 'createTime', valueType: 'dateTime', search: false },
    {
      title: '操作',
      valueType: 'option',
      width: 100,
      render: (_, record) =>
        canEditRole
          ? [
              <a
                key="role"
                onClick={() => {
                  setEditing(record);
                  setOpen(true);
                }}
              >
                改角色
              </a>,
            ]
          : ['-'],
    },
  ];

  return (
    <PageContainer>
      <ProTable<User>
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        request={(params) => toProTableResult(pageUsers, params)}
        search={false}
        scroll={{ x: 1000 }}
      />

      <ModalForm<{ role: number }>
        title="修改用户角色"
        open={open}
        onOpenChange={setOpen}
        initialValues={{ role: editing?.role }}
        modalProps={{ destroyOnClose: true }}
        onFinish={handleSubmit}
        width={400}
      >
        <ProFormSelect
          name="role"
          label="目标角色"
          options={editableRoleOptions}
          tooltip="仅允许 普通用户 / 清运员 / 设备管理员"
          rules={[{ required: true, message: '请选择角色' }]}
        />
      </ModalForm>
    </PageContainer>
  );
}
