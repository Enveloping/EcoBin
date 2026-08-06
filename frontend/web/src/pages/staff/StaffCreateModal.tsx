import { useEffect, useState } from 'react';
import { ModalForm, ProFormText } from '@ant-design/pro-components';
import { App, Form } from 'antd';
import {
  createStaffAccount,
  getCurrentEffectiveAccess,
  listPermissionDefinitions,
  type DirectoryContext,
} from '@/api/identityDirectory';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { useAuthStore } from '@/stores/authStore';
import type {
  EffectiveAccess,
  PermissionDefinition,
  StaffAccount,
} from '@/types';
import PermissionTreeSelector from './PermissionTreeSelector';

export interface StaffCreateFormValue {
  loginName: string;
  initialPassword: string;
  displayName: string;
  contactPhone?: string;
  permissionCodes: string[];
}

interface StaffCreateModalProps {
  context: DirectoryContext | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (staff: StaffAccount) => void | Promise<void>;
}

export default function StaffCreateModal({
  context,
  open,
  onOpenChange,
  onCreated,
}: StaffCreateModalProps) {
  const { message } = App.useApp();
  const executeCommand = useCommandExecutor();
  const canGrant = useAuthStore((state) =>
    state.hasCapability('permission.manage'));
  const session = useAuthStore((state) => state.session);
  const operatorNeedsDelegationLimit = session?.accountType === 'STAFF';
  const [definitions, setDefinitions] = useState<PermissionDefinition[]>([]);
  const [operatorAccess, setOperatorAccess] =
    useState<EffectiveAccess | null>(null);

  useEffect(() => {
    if (!open || !context || !canGrant) {
      setDefinitions([]);
      setOperatorAccess(null);
      return;
    }
    let current = true;
    void Promise.all([
      listPermissionDefinitions(context),
      operatorNeedsDelegationLimit
        ? getCurrentEffectiveAccess()
        : Promise.resolve(null),
    ]).then(([nextDefinitions, nextOperatorAccess]) => {
      if (!current) return;
      setDefinitions(nextDefinitions);
      setOperatorAccess(nextOperatorAccess);
    }).catch(() => {
      if (!current) return;
      setDefinitions([]);
      setOperatorAccess(null);
    });
    return () => {
      current = false;
    };
  }, [canGrant, context, open, operatorNeedsDelegationLimit]);

  const submit = async (values: StaffCreateFormValue) => {
    if (!context) return false;
    const payload = {
      loginName: values.loginName,
      initialPassword: values.initialPassword,
      displayName: values.displayName,
      contactPhone: values.contactPhone,
      permissionCodes: values.permissionCodes ?? [],
    };
    const created = await executeCommand(
      commandKey('create-staff', values.loginName, payload),
      (intent) => createStaffAccount(context, payload, intent),
    );
    message.success('工作人员账号已创建');
    await onCreated(created);
    onOpenChange(false);
    return true;
  };

  return (
    <ModalForm<StaffCreateFormValue>
      title="创建工作人员"
      open={open}
      onOpenChange={onOpenChange}
      initialValues={{ permissionCodes: [] }}
      modalProps={{ destroyOnClose: true, width: 760 }}
      submitter={{
        searchConfig: { submitText: '创建', resetText: '取消' },
      }}
      onFinish={submit}
    >
      <ProFormText
        name="loginName"
        label="全局登录名"
        rules={[
          { required: true },
          {
            pattern: /^[a-z0-9][a-z0-9._-]*$/,
            message: '请输入规范化小写登录名',
          },
        ]}
      />
      <ProFormText.Password
        name="initialPassword"
        label="初始密码"
        rules={[{ required: true }, { min: 8 }]}
      />
      <ProFormText
        name="displayName"
        label="展示名"
        rules={[{ required: true }]}
      />
      <ProFormText name="contactPhone" label="联系电话" />
      {canGrant && (
        <Form.Item
          name="permissionCodes"
          label="初始租户权限"
          tooltip="只能授予当前操作者在相同或更大作用域拥有的权限。"
        >
          <PermissionTreeSelector
            ariaLabel="初始租户权限"
            definitions={definitions}
            scopeKind="TENANT"
            delegablePermissionCodes={
              operatorNeedsDelegationLimit
                ? operatorAccess?.tenantPermissionCodes ?? []
                : undefined
            }
            defaultExpandAll={false}
          />
        </Form.Item>
      )}
    </ModalForm>
  );
}
