import { useEffect, useState } from 'react';
import { PageContainer } from '@ant-design/pro-components';
import { Card, Col, Row, Statistic, Table, Spin } from 'antd';
import {
  InboxOutlined,
  DashboardOutlined,
  TeamOutlined,
  HddOutlined,
} from '@ant-design/icons';
import { statDashboard, statDeviceRanking } from '@/api/statistics';
import { statLabel } from '@/constants';
import { statCardGradients } from '@/theme';
import { pageHeader } from '@/utils/pageStyle';
import CountUp from '@/components/CountUp';
import AuroraBackground from '@/components/AuroraBackground';
import MouseSpotlight from '@/components/MouseSpotlight';
import { useTilt } from '@/hooks/useTilt';
import { palette } from '@/theme';

const ICON_MAP: Record<string, React.ReactNode> = {
  deliveryCount: <InboxOutlined />,
  todayMemberCount: <TeamOutlined />,
  totalCount: <HddOutlined />,
  totalWeight: <DashboardOutlined />,
};

const GRADIENTS = [
  statCardGradients.primary,
  statCardGradients.info,
  statCardGradients.success,
  statCardGradients.warning,
];

/** 单张统计卡片：3D 倾斜 + 数字滚动 */
function StatCard({
  label,
  value,
  icon,
  gradient,
}: {
  label: string;
  value: number | string;
  icon?: React.ReactNode;
  gradient: string;
}) {
  const tilt = useTilt(8);
  return (
    <Card
      bordered={false}
      style={{
        background: gradient,
        borderRadius: 12,
        minHeight: 120,
        cursor: 'default',
        position: 'relative',
        overflow: 'hidden',
        transition: 'transform 0.15s ease-out',
        transformStyle: 'preserve-3d',
      }}
      styles={{ body: { padding: 20 } }}
      onMouseMove={tilt.onMouseMove}
      onMouseLeave={tilt.onMouseLeave}
    >
      <Statistic
        title={
          <span style={{ color: 'rgba(255,255,255,0.85)', fontSize: 14 }}>{label}</span>
        }
        value={value}
        valueRender={() => (
          <span style={{ color: '#FFFFFF', fontWeight: 600, fontSize: 28 }}>
            <CountUp value={value} duration={1400} />
          </span>
        )}
        prefix={icon ? (
          <span style={{ marginRight: 8, opacity: 0.9, fontSize: 28 }}>{icon}</span>
        ) : undefined}
      />
    </Card>
  );
}

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
    <PageContainer {...pageHeader('工作台', '数据概览与运营状态')}>
      {/* 极光背景 + 鼠标光晕 */}
      <AuroraBackground variant="teal" opacity={0.18} />
      <MouseSpotlight color={palette.primaryRGB} size={450} intensity={0.1} />
      <div style={{ position: 'relative', zIndex: 1 }}>
      <Spin spinning={loading}>
        {/* 统计卡片 */}
        {overviewEntries.length > 0 && (
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            {overviewEntries.map(([k, v], idx) => (
              <Col key={k} xs={24} sm={12} lg={6}>
                <StatCard
                  label={statLabel(k)}
                  value={v as number | string}
                  icon={ICON_MAP[k]}
                  gradient={GRADIENTS[idx % GRADIENTS.length]}
                />
              </Col>
            ))}
          </Row>
        )}
        {overviewEntries.length === 0 && !loading && (
          <Card
            bordered={false}
            style={{ marginBottom: 24, borderRadius: 12, textAlign: 'center' }}
          >
            <span style={{ color: '#94A3B8' }}>暂无数据</span>
          </Card>
        )}

        {/* 排行榜 */}
        <Card
          title={
            <span style={{ fontWeight: 600 }}>
              本月设备投递排行
            </span>
          }
          bordered={false}
          style={{ borderRadius: 12 }}
        >
          <Table
            rowKey={(_, i) => String(i)}
            size="middle"
            columns={rankingColumns}
            dataSource={ranking}
            pagination={false}
            locale={{ emptyText: '暂无数据' }}
          />
        </Card>
      </Spin>
      </div>
    </PageContainer>
  );
}
