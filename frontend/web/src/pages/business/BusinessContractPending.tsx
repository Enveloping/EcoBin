import { PageContainer, ProCard } from '@ant-design/pro-components';
import { Alert, Descriptions, Tag, Typography } from 'antd';
import { useSearchParams } from 'react-router-dom';
import { pageHeader } from '@/utils/pageStyle';

export type PendingBusinessKind = 'delivery' | 'cleaning' | 'withdrawal';

const COPY: Record<
  PendingBusinessKind,
  { title: string; resource: string; contract: string }
> = {
  delivery: {
    title: '投递订单',
    resource: '投递订单、审核与纠正',
    contract: '投递 Web 查询 paths 与订单响应 schema',
  },
  cleaning: {
    title: '清运订单',
    resource: '清运操作、清运记录与异常',
    contract: '清运记录 Web 查询 paths 与结果状态 schema',
  },
  withdrawal: {
    title: '提现订单',
    resource: '提现审核、渠道状态与资金释放',
    contract: '提现 Web 查询 paths、资金状态与安全渠道摘要',
  },
};

export default function BusinessContractPendingPage({
  kind,
}: {
  kind: PendingBusinessKind;
}) {
  const [searchParams] = useSearchParams();
  const copy = COPY[kind];
  const context = [
    ['租户', searchParams.get('tenant')],
    ['机构', searchParams.get('organization')],
    ['机构用户', searchParams.get('organizationUserUid')],
  ].filter((entry): entry is [string, string] => !!entry[1]);

  return (
    <PageContainer
      {...pageHeader(
        copy.title,
        '导航和作用域深链已经就绪，数据接入以目标 OpenAPI 为准。',
      )}
    >
      <ProCard bordered className="contract-pending-card">
        <Alert
          showIcon
          type="info"
          message={`${copy.resource}尚未开放可运行的 Web 接口`}
          description={
            <>
              当前后端没有{copy.contract}。本页不会调用旧接口，也不会把
              “接口未实现”伪装成空数据；契约落地后可沿用当前 URL
              中的租户、机构和用户筛选。
            </>
          }
        />
        {context.length > 0 && (
          <Descriptions
            title="已应用的关联上下文"
            column={1}
            size="small"
            style={{ marginTop: 24 }}
          >
            {context.map(([label, value]) => (
              <Descriptions.Item key={label} label={label}>
                <Tag>{value}</Tag>
              </Descriptions.Item>
            ))}
          </Descriptions>
        )}
        <Typography.Paragraph type="secondary" style={{ margin: '24px 0 0' }}>
          需要后端先提供可分页查询、正式状态枚举、作用域授权和错误响应，
          再生成 TypeScript 类型并接入表格。
        </Typography.Paragraph>
      </ProCard>
    </PageContainer>
  );
}
