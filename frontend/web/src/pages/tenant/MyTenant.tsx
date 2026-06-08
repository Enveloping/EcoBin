import { useEffect, useState } from 'react';
import { PageContainer, ProDescriptions } from '@ant-design/pro-components';
import { Card, Spin } from 'antd';
import { getMyTenant } from '@/api/tenant';
import EnumTag from '@/components/EnumTag';
import { STATUS } from '@/constants';
import type { Tenant } from '@/types';

export default function MyTenant() {
  const [data, setData] = useState<Tenant | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMyTenant()
      .then(setData)
      .finally(() => setLoading(false));
  }, []);

  return (
    <PageContainer>
      <Card>
        <Spin spinning={loading}>
          {data && (
            <ProDescriptions column={2} title="租户资料">
              <ProDescriptions.Item label="租户名称">{data.name}</ProDescriptions.Item>
              <ProDescriptions.Item label="租户编码">{data.code || '-'}</ProDescriptions.Item>
              <ProDescriptions.Item label="登录用户名">
                {data.username || '-'}
              </ProDescriptions.Item>
              <ProDescriptions.Item label="小程序 AppID">
                {data.miniappAppid || '-'}
              </ProDescriptions.Item>
              <ProDescriptions.Item label="微信商户号">
                {data.merchantNo || '-'}
              </ProDescriptions.Item>
              <ProDescriptions.Item label="联系人">
                {data.contactName || '-'}
              </ProDescriptions.Item>
              <ProDescriptions.Item label="联系电话">
                {data.contactPhone || '-'}
              </ProDescriptions.Item>
              <ProDescriptions.Item label="地址">{data.address || '-'}</ProDescriptions.Item>
              <ProDescriptions.Item label="状态">
                <EnumTag map={STATUS} value={data.status} />
              </ProDescriptions.Item>
            </ProDescriptions>
          )}
        </Spin>
      </Card>
    </PageContainer>
  );
}
