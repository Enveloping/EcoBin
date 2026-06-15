import { useRef, useState } from 'react';
import {
  PageContainer,
  ProTable,
  ModalForm,
  ProFormTextArea,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { App, Tag } from 'antd';
import { pageWithdraws, auditWithdraw } from '@/api/withdraw';
import { toProTableResult } from '@/utils/proTable';
import { pageHeader, proTableConfig, DANGER_COLOR } from '@/utils/pageStyle';
import { WITHDRAW_STATUS, toValueEnum } from '@/constants';
import type { WithdrawOrder } from '@/types';

export default function WithdrawPage() {
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<{ id: number; pass: boolean } | null>(null);

  const openAudit = (id: number, pass: boolean) => {
    setTarget({ id, pass });
    setOpen(true);
  };

  const handleSubmit = async (values: { remark?: string }) => {
    if (!target) return false;
    await auditWithdraw(target.id, target.pass, values.remark);
    message.success(target.pass ? '已通过' : '已驳回');
    setOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const columns: ProColumns<WithdrawOrder>[] = [
    { title: 'ID', dataIndex: 'id', width: 70, search: false },
    { title: '申请用户', dataIndex: 'userId', width: 90, search: false },
    { title: '金额(元)', dataIndex: 'amount', valueType: 'money', search: false },
    {
      title: '状态',
      dataIndex: 'status',
      valueEnum: toValueEnum(WITHDRAW_STATUS),
      valueType: 'select',
    },
    { title: '审核人', dataIndex: 'auditBy', width: 90, search: false },
    { title: '审核时间', dataIndex: 'auditTime', valueType: 'dateTime', search: false },
    { title: '审核备注', dataIndex: 'auditRemark', search: false, ellipsis: true },
    {
      title: '转账单号',
      dataIndex: 'transferNo',
      search: false,
      render: (_, r) => r.transferNo || <Tag>未转账</Tag>,
    },
    { title: '申请时间', dataIndex: 'createTime', valueType: 'dateTime', search: false },
    {
      title: '操作',
      valueType: 'option',
      width: 140,
      render: (_, record) =>
        record.status === 0
          ? [
              <a key="pass" onClick={() => openAudit(record.id, true)}>
                通过
              </a>,
              <a key="reject" style={{ color: DANGER_COLOR }} onClick={() => openAudit(record.id, false)}>
                驳回
              </a>,
            ]
          : ['已处理'],
    },
  ];

  return (
    <PageContainer {...pageHeader('提现审核', '处理用户提现申请')}>
      <ProTable<WithdrawOrder>
        {...proTableConfig}
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        scroll={{ x: 1300 }}
        request={(params) => toProTableResult(pageWithdraws, params)}
      />

      <ModalForm<{ remark?: string }>
        title={target?.pass ? '通过提现申请' : '驳回提现申请'}
        open={open}
        onOpenChange={setOpen}
        modalProps={{ destroyOnClose: true }}
        width={420}
        onFinish={handleSubmit}
      >
        <ProFormTextArea
          name="remark"
          label="审核备注"
          placeholder="可选，填写审核说明"
          fieldProps={{ rows: 3 }}
        />
      </ModalForm>
    </PageContainer>
  );
}
