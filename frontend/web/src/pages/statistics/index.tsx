import { useEffect, useState } from 'react';
import { PageContainer } from '@ant-design/pro-components';
import { Statistic, Row, Col, Spin, Empty } from 'antd';
import {
  statDevices,
  statMembers,
  statDelivery,
  statClean,
  statPayout,
  statMemberMoney,
} from '@/api/statistics';
import { statLabel } from '@/constants';
import { pageHeader } from '@/utils/pageStyle';
import { statCardGradients } from '@/theme';
import CountUp from '@/components/CountUp';

interface Block {
  title: string;
  data: Record<string, unknown>;
  gradient: string;
}

const GRADIENT_LIST = [
  statCardGradients.primary,
  statCardGradients.info,
  statCardGradients.success,
  statCardGradients.warning,
];

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
            gradient: GRADIENT_LIST[i % GRADIENT_LIST.length],
          })),
        );
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <PageContainer {...pageHeader('业务统计', '各维度运营数据汇总')}>
      <Spin spinning={loading}>
        <Row gutter={[16, 16]}>
          {blocks.map((b) => {
            const entries = Object.entries(b.data);
            return (
              <Col key={b.title} xs={24} md={12}>
                <div
                  style={{
                    background: '#FFFFFF',
                    borderRadius: 12,
                    overflow: 'hidden',
                    boxShadow: '0 1px 2px rgba(0,0,0,0.03), 0 1px 6px -1px rgba(0,0,0,0.02)',
                  }}
                >
                  <div
                    style={{
                      background: b.gradient,
                      padding: '14px 20px',
                      color: '#FFFFFF',
                      fontWeight: 600,
                      fontSize: 15,
                    }}
                  >
                    {b.title}
                  </div>
                  <div style={{ padding: 20, minHeight: 120 }}>
                    {entries.length > 0 ? (
                      <Row gutter={[16, 16]}>
                        {entries.map(([k, v]) => (
                          <Col key={k} span={12}>
                            <Statistic
                              title={<span style={{ fontSize: 13 }}>{statLabel(k)}</span>}
                              value={v as number | string}
                              valueRender={() => (
                                <span style={{ fontWeight: 600, color: '#0F172A', fontSize: 22 }}>
                                  <CountUp value={v as number | string} duration={1200} />
                                </span>
                              )}
                            />
                          </Col>
                        ))}
                      </Row>
                    ) : (
                      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />
                    )}
                  </div>
                </div>
              </Col>
            );
          })}
        </Row>
      </Spin>
    </PageContainer>
  );
}
