import { useEffect, useState } from 'react';
import { PageContainer, ProTable, type ProColumns } from '@ant-design/pro-components';
import { Card, Statistic, Row, Col } from 'antd';
import { pageDeliveries, deliveryTodayOverview } from '@/api/delivery';
import { toProTableResult } from '@/utils/proTable';
import {
  WASTE_TYPE1,
  WASTE_TYPE2,
  DELIVERY_STATUS,
  DELIVERY_ABNORMAL,
  LOGIN_TYPE,
  toValueEnum,
  statLabel,
} from '@/constants';
import type { DeliveryOrder } from '@/types';

export default function DeliveryPage() {
  const [overview, setOverview] = useState<Record<string, unknown>>({});

  useEffect(() => {
    deliveryTodayOverview()
      .then(setOverview)
      .catch(() => undefined);
  }, []);

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
    { title: '投递时间', dataIndex: 'createTime', valueType: 'dateTime', search: false },
  ];

  const overviewEntries = Object.entries(overview);

  return (
    <PageContainer>
      {overviewEntries.length > 0 && (
        <Card style={{ marginBottom: 16 }} title="今日投递概览">
          <Row gutter={16}>
            {overviewEntries.map(([k, v]) => (
              <Col key={k} span={6}>
                <Statistic title={statLabel(k)} value={v as number | string} />
              </Col>
            ))}
          </Row>
        </Card>
      )}
      <ProTable<DeliveryOrder>
        rowKey="id"
        columns={columns}
        search={false}
        scroll={{ x: 1400 }}
        request={(params) => toProTableResult(pageDeliveries, params)}
      />
    </PageContainer>
  );
}
