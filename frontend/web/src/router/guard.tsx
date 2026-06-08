import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Result, Button } from 'antd';
import { useAuthStore } from '@/stores/authStore';
import { defaultPathFor } from './routes';

/** 未登录 → 跳登录页 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const location = useLocation();
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

/** 角色不匹配 → 403 */
export function RoleGuard({ roles, children }: { roles: number[]; children: ReactNode }) {
  const role = useAuthStore((s) => s.role);
  if (role == null || !roles.includes(role)) {
    return (
      <Result
        status="403"
        title="403"
        subTitle="抱歉，当前角色无权访问此页面。"
        extra={
          <Button type="primary" href={defaultPathFor(role)}>
            返回首页
          </Button>
        }
      />
    );
  }
  return <>{children}</>;
}
