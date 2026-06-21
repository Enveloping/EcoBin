import { useEffect, useRef, useState } from 'react';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import { Statistic, Row, Col, Popconfirm, App, Image, Space } from 'antd';
import { InboxOutlined, DashboardOutlined } from '@ant-design/icons';
import { pageDeliveries, deliveryTodayOverview, auditDelivery } from '@/api/delivery';
import { toProTableResult } from '@/utils/proTable';
import { pageHeader, proTableConfig, DANGER_COLOR } from '@/utils/pageStyle';
import { statCardGradients } from '@/theme';
import CountUp from '@/components/CountUp';
import {
  WASTE_TYPE1,
  WASTE_TYPE2,
  DELIVERY_STATUS,
  DELIVERY_ABNORMAL,
  AUDIT_STATUS,
  LOGIN_TYPE,
  toValueEnum,
  statLabel,
} from '@/constants';
import type { DeliveryOrder } from '@/types';

const ICON_MAP: Record<string, React.ReactNode> = {
  deliveryCount: <InboxOutlined />,
  deliveryWeight: <DashboardOutlined />,
};

const GRADIENTS = [
  statCardGradients.primary,
  statCardGradients.info,
  statCardGradients.success,
  statCardGradients.warning,
];

export default function DeliveryPage() {
  const [overview, setOverview] = useState<Record<string, unknown>>({});
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();

  useEffect(() => {
    deliveryTodayOverview()
      .then(setOverview)
      .catch(() => undefined);
  }, []);

  const handleAudit = async (id: number, pass: boolean) => {
    await auditDelivery(id, pass ? 1 : 2);
    message.success(pass ? '已通过' : '已拒绝');
    actionRef.current?.reload();
  };

  const columns: ProColumns<DeliveryOrder>[] = [
    { title: 'ID', dataIndex: 'id', width: 70, search: false },
    { title: '订单编号', dataIndex: 'orderSn', copyable: true, ellipsis: true },
    { title: '设备', dataIndex: 'deviceId', width: 70, search: false },
    { title: '投口', dataIndex: 'doorId', width: 70, search: false },
    { title: '用户', dataIndex: 'userId', width: 70, search: false },
    { title: '一级分类', dataIndex: 'wasteType1', valueEnum: toValueEnum(WASTE_TYPE1), search: false },
    { title: '二级分类', dataIndex: 'wasteType2', valueEnum: toValueEnum(WASTE_TYPE2), search: false },
    { title: '重量(kg)', dataIndex: 'weight', search: false },
    { title: '单价', dataIndex: 'price', valueType: 'money', search: false },
    { title: '积分', dataIndex: 'score', width: 70, search: false },
    { title: '登录方式', dataIndex: 'loginType', valueEnum: toValueEnum(LOGIN_TYPE), search: false },
    {
      title: '投递阶段',
      dataIndex: 'deliveryStatus',
      valueEnum: toValueEnum(DELIVERY_STATUS),
      search: false,
    },
    {
      title: '状态',
      dataIndex: 'status',
      valueEnum: toValueEnum(DELIVERY_ABNORMAL),
      search: false,
    },
    {
      title: '审核状态',
      dataIndex: 'auditStatus',
      valueEnum: toValueEnum(AUDIT_STATUS),
      search: false,
    },
    {
      title: '照片',
      width: 200,
      search: false,
      render: (_, record) => {
        const photos = [
          { src: record.photoOpenOutside, label: '开门·箱外' },
          { src: record.photoOpenInside, label: '开门·箱内' },
          { src: record.photoCloseOutside, label: '关门·箱外' },
          { src: record.photoCloseInside, label: '关门·箱内' },
        ].filter((p) => p.src);
        if (photos.length === 0) return '—';
        return (
          <Image.PreviewGroup>
            <Space size={4}>
              {photos.map((p) => (
                <Image
                  key={p.label}
                  src={p.src}
                  alt={p.label}
                  title={p.label}
                  width={36}
                  height={36}
                  style={{ objectFit: 'cover', borderRadius: 4 }}
                />
              ))}
            </Space>
          </Image.PreviewGroup>
        );
      },
    },
    { title: '投递时间', dataIndex: 'createTime', valueType: 'dateTime', search: false },
    {
      title: '操作',
      valueType: 'option',
      fixed: 'right',
      width: 140,
      render: (_, record) =>
        record.auditStatus === 0
          ? [
              <Popconfirm
                key="pass"
                title="确认审核通过？通过后返现金额将入账用户余额。"
                onConfirm={() => handleAudit(record.id, true)}
              >
                <a>通过</a>
              </Popconfirm>,
              <Popconfirm
                key="reject"
                title="确认拒绝该投递单？拒绝后不返现。"
                onConfirm={() => handleAudit(record.id, false)}
              >
                <a style={{ color: DANGER_COLOR }}>拒绝</a>
              </Popconfirm>,
            ]
          : ['已处理'],
    },
  ];

  const overviewEntries = Object.entries(overview);

  return (
    <PageContainer {...pageHeader('投递订单', '查看用户投递记录与今日数据概览')}>
      {overviewEntries.length > 0 && (
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          {overviewEntries.map(([k, v], idx) => (
            <Col key={k} xs={24} sm={12} lg={6}>
              <div
                style={{
                  background: GRADIENTS[idx % GRADIENTS.length],
                  borderRadius: 12,
                  padding: 20,
                  minHeight: 100,
                }}
              >
                <Statistic
                  title={
                    <span style={{ color: 'rgba(255,255,255,0.85)', fontSize: 14 }}>
                      {statLabel(k)}
                    </span>
                  }
                  value={v as number | string}
                  valueRender={() => (
                    <span style={{ color: '#FFFFFF', fontWeight: 600, fontSize: 26 }}>
                      <CountUp value={v as number | string} duration={1200} />
                    </span>
                  )}
                  prefix={ICON_MAP[k] ? (
                    <span style={{ marginRight: 8, opacity: 0.9 }}>{ICON_MAP[k]}</span>
                  ) : undefined}
                />
              </div>
            </Col>
          ))}
        </Row>
      )}
      <ProTable<DeliveryOrder>
        {...proTableConfig}
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        search={false}
        scroll={{ x: 1800 }}
        request={(params) => toProTableResult(pageDeliveries, params)}
      />
    </PageContainer>
  );
}
