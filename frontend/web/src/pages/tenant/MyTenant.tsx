import { useEffect, useState } from 'react';
import {
  ModalForm,
  PageContainer,
  ProDescriptions,
  ProFormText,
} from '@ant-design/pro-components';
import { App, Button, Spin, Tag } from 'antd';
import {
  getCurrentTenant,
  updateCurrentTenant,
  type TenantProfileInput,
} from '@/api/identityDirectory';
import { useAuthStore } from '@/stores/authStore';
import { pageHeader } from '@/utils/pageStyle';
import type { IdentityTenant } from '@/types';

export default function MyTenant() {
  const { message } = App.useApp();
  const canManage = useAuthStore((state) =>
    state.hasCapability('tenant.manage'));
  const [data, setData] = useState<IdentityTenant | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setData(await getCurrentTenant());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const submit = async (
    values: Omit<TenantProfileInput, 'expectedVersion'>,
  ) => {
    if (!data) return false;
    const updated = await updateCurrentTenant({
      ...values,
      expectedVersion: data.version,
    });
    setData(updated);
    setEditing(false);
    message.success('租户资料已更新');
    return true;
  };

  return (
    <PageContainer
      {...pageHeader('我的租户', '资料来自当前服务端会话租户，客户端不能切换作用域。')}
      extra={
        canManage
          ? [<Button key="edit" onClick={() => setEditing(true)}>编辑资料</Button>]
          : undefined
      }
    >
      <Spin spinning={loading}>
        {data && (
          <ProDescriptions column={2} title="租户资料" bordered>
            <ProDescriptions.Item label="租户编码">
              {data.tenantCode}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="企业名称">
              {data.enterpriseName}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="联系人">
              {data.contactName || '-'}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="联系电话">
              {data.contactPhone || '-'}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="地址">
              {data.contactAddress || '-'}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="状态">
              <Tag color={data.status === 'ENABLED' ? 'green' : 'default'}>
                {data.status === 'ENABLED' ? '已启用' : '已停用'}
              </Tag>
            </ProDescriptions.Item>
            <ProDescriptions.Item label="资源版本">
              {data.version}
            </ProDescriptions.Item>
          </ProDescriptions>
        )}
      </Spin>

      <ModalForm<Omit<TenantProfileInput, 'expectedVersion'>>
        title="编辑租户资料"
        open={editing}
        onOpenChange={setEditing}
        initialValues={data ?? undefined}
        modalProps={{ destroyOnClose: true }}
        onFinish={submit}
      >
        <ProFormText
          name="enterpriseName"
          label="企业名称"
          rules={[{ required: true }]}
        />
        <ProFormText name="contactName" label="联系人" />
        <ProFormText name="contactPhone" label="联系电话" />
        <ProFormText name="contactAddress" label="联系地址" />
      </ModalForm>
    </PageContainer>
  );
}
