import { PageContainer } from '@ant-design/pro-components';
import {
  Card,
  Empty,
  Select,
  Space,
  Spin,
  Typography,
} from 'antd';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import OrganizationDeliveryConfiguration from '@/pages/organization/OrganizationDeliveryConfiguration';
import { pageHeader } from '@/utils/pageStyle';

export default function DeliveryConfigurationPage() {
  const directoryScope = useDirectoryScope();
  const organizationScope = useOrganizationScope(directoryScope);

  const content = (() => {
    if (directoryScope.loading || organizationScope.loading) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在加载机构范围" />
        </div>
      );
    }
    if (!directoryScope.context) {
      return <Empty description="请选择目标租户" />;
    }
    if (!organizationScope.organizationOptions.length) {
      return <Empty description="当前租户尚无机构" />;
    }
    if (!organizationScope.organizationCode) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在应用机构范围" />
        </div>
      );
    }
    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card size="small">
          <Space wrap>
            <Typography.Text strong>目标机构</Typography.Text>
            <Select
              aria-label="目标机构"
              showSearch
              optionFilterProp="label"
              style={{ width: 360 }}
              value={organizationScope.organizationCode}
              options={organizationScope.organizationOptions}
              onChange={organizationScope.setOrganizationCode}
            />
          </Space>
        </Card>
        <OrganizationDeliveryConfiguration
          active
          context={directoryScope.context}
          organizationCode={organizationScope.organizationCode}
        />
      </Space>
    );
  })();

  return (
    <PageContainer
      {...pageHeader(
        '投递与审核规则',
        '按机构发布不可变规则版本；已经开始的投递继续使用开始时冻结的旧版本。',
      )}
    >
      <DirectoryScopeBar scope={directoryScope} />
      {content}
    </PageContainer>
  );
}
