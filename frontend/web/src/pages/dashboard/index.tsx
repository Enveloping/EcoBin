import { useEffect, useState } from 'react';
import { PageContainer } from '@ant-design/pro-components';
import { Card, Col, Row, Statistic, Table, Spin } from 'antd';
import { statDashboard, statDeviceRanking } from '@/api/statistics';
import { statLabel } from '@/constants';

export default function Dashboard() {
  const [overview, setOverview] = useState<Record<string, unknown>>({});
  const [ranking, setRanking] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([statDashboard(), statDeviceRanking(10)])
      .then(([o, r]) => {
        if (o.status === 'fulfilled') setOverview(o.value);
        if (r.status === 'fulfilled') setRanking(r.value);
      })
      .finally(() => setLoading(false));
  }, []);

  const overviewEntries = Object.entries(overview);
  const rankingColumns =
    ranking.length > 0
      ? Object.keys(ranking[0]).map((k) => ({ title: k, dataIndex: k, key: k }))
      : [];

  return (
    <PageContainer>
      <Spin spinning={loading}>
        <Card title="今日概览" style={{ marginBottom: 16 }}>
          {overviewEntries.length > 0 ? (
            <Row gutter={16}>
              {overviewEntries.map(([k, v]) => (
                <Col key={k} span={6}>
                  <Statistic title={statLabel(k)} value={v as number | string} />
                </Col>
              ))}
            </Row>
          ) : (
            <span style={{ color: '#999' }}>暂无数据</span>
          )}
        </Card>

        <Card title="本月设备投递排行">
          <Table
            rowKey={(_, i) => String(i)}
            size="small"
            columns={rankingColumns}
            dataSource={ranking}
            pagination={false}
            locale={{ emptyText: '暂无数据' }}
          />
        </Card>
      </Spin>
    </PageContainer>
  );
}
