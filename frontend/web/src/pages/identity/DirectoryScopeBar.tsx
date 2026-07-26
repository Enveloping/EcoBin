import { Alert, Select, Space, Spin, Typography } from 'antd';
import type { DirectoryScope } from './useDirectoryScope';

export default function DirectoryScopeBar({ scope }: { scope: DirectoryScope }) {
  if (!scope.platform) return null;
  if (scope.loading) return <Spin size="small" />;
  if (!scope.tenantOptions.length) {
    return (
      <Alert
        type="info"
        showIcon
        message="尚无租户"
        description="请先在租户管理页面创建目标租户。"
      />
    );
  }
  return (
    <Space style={{ marginBottom: 16 }}>
      <Typography.Text strong>平台目标租户</Typography.Text>
      <Select
        showSearch
        optionFilterProp="label"
        style={{ width: 360 }}
        value={scope.tenantCode}
        options={scope.tenantOptions}
        onChange={scope.setTenantCode}
      />
    </Space>
  );
}
