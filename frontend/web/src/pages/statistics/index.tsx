import { useEffect, useState } from 'react';
import { PageContainer } from '@ant-design/pro-components';
import { Card, Col, Row, Statistic, Spin, Empty } from 'antd';
import {
  statDevices,
  statMembers,
  statDelivery,
  statClean,
  statPayout,
  statMemberMoney,
} from '@/api/statistics';
import { statLabel } from '@/constants';

interface Block {
  title: string;
  data: Record<string, unknown>;
}

const loaders: { title: string; fn: () => Promise<Record<string, unknown>> }[] = [
  { title: '设备统计', fn: statDevices },
  { title: '会员统计', fn: statMembers },
  { title: '本月投递统计', fn: statDelivery },
  { title: '本月清运统计', fn: statClean },
  { title: '提现支出统计', fn: statPayout },
  { title: '会员资金统计', fn: statMemberMoney },
];

export default function StatisticsPage() {
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled(loaders.map((l) => l.fn()))
      .then((results) => {
        setBlocks(
          results.map((res, i) => ({
            title: loaders[i].title,
            data: res.status === 'fulfilled' ? res.value : {},
          })),
        );
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <PageContainer>
      <Spin spinning={loading}>
        <Row gutter={[16, 16]}>
          {blocks.map((b) => {
            const entries = Object.entries(b.data);
            return (
              <Col key={b.title} xs={24} md={12}>
                <Card title={b.title}>
                  {entries.length > 0 ? (
                    <Row gutter={16}>
                      {entries.map(([k, v]) => (
                        <Col key={k} span={12} style={{ marginBottom: 12 }}>
                          <Statistic title={statLabel(k)} value={v as number | string} />
                        </Col>
                      ))}
                    </Row>
                  ) : (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />
                  )}
                </Card>
              </Col>
            );
          })}
        </Row>
      </Spin>
    </PageContainer>
  );
}
