import { useRef } from 'react';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { Popconfirm, App } from 'antd';
import { pageCleans, auditClean } from '@/api/clean';
import { toProTableResult } from '@/utils/proTable';
import {
  WASTE_TYPE1,
  WASTE_TYPE2,
  AUDIT_STATUS,
  CLEAN_STATUS,
  toValueEnum,
} from '@/constants';
import type { CleanOrder } from '@/types';

export default function CleanPage() {
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();

  const handleAudit = async (id: number, pass: boolean) => {
    await auditClean(id, pass ? 1 : 2);
    message.success(pass ? '已通过' : '已拒绝');
    actionRef.current?.reload();
  };

  const columns: ProColumns<CleanOrder>[] = [
    { title: 'ID', dataIndex: 'id', width: 70, search: false },
    { title: '订单编号', dataIndex: 'orderSn', copyable: true, ellipsis: true },
    { title: '设备', dataIndex: 'deviceId', width: 70, search: false },
    { title: '投口', dataIndex: 'doorId', width: 70, search: false },
    { title: '清运员', dataIndex: 'userId', width: 70, search: false },
    { title: '一级分类', dataIndex: 'wasteType1', valueEnum: toValueEnum(WASTE_TYPE1), search: false },
    { title: '二级分类', dataIndex: 'wasteType2', valueEnum: toValueEnum(WASTE_TYPE2), search: false },
    { title: '重量(kg)', dataIndex: 'weight', search: false },
    {
      title: '审核状态',
      dataIndex: 'auditStatus',
      valueEnum: toValueEnum(AUDIT_STATUS),
    },
    { title: '单据状态', dataIndex: 'status', valueEnum: toValueEnum(CLEAN_STATUS), search: false },
    { title: '提交时间', dataIndex: 'createTime', valueType: 'dateTime', search: false },
    {
      title: '操作',
      valueType: 'option',
      width: 140,
      render: (_, record) =>
        record.auditStatus === 0
          ? [
              <Popconfirm
                key="pass"
                title="确认审核通过？"
                onConfirm={() => handleAudit(record.id, true)}
              >
                <a>通过</a>
              </Popconfirm>,
              <Popconfirm
                key="reject"
                title="确认拒绝该清运单？"
                onConfirm={() => handleAudit(record.id, false)}
              >
                <a style={{ color: '#ff4d4f' }}>拒绝</a>
              </Popconfirm>,
            ]
          : ['已处理'],
    },
  ];

  return (
    <PageContainer>
      <ProTable<CleanOrder>
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        search={{ labelWidth: 'auto' }}
        scroll={{ x: 1300 }}
        request={(params) => toProTableResult(pageCleans, params)}
      />
    </PageContainer>
  );
}
