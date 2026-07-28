import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Result, Button, Skeleton } from 'antd';
import { useAuthStore } from '@/stores/authStore';
import {
  canAccessRoute,
  defaultPathFor,
  type AppRoute,
} from './routes';

export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();
  if (status === 'checking') {
    return (
      <div className="route-loading" aria-label="正在载入会话">
        <Skeleton active paragraph={{ rows: 4 }} />
      </div>
    );
  }
  if (status !== 'authenticated') {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

export function CapabilityGuard({
  route,
  children,
}: {
  route: Pick<AppRoute, 'allOf' | 'anyOf' | 'accountTypes'>;
  children: ReactNode;
}) {
  const session = useAuthStore((state) => state.session);
  if (!canAccessRoute(session, route)) {
    return (
      <Result
        status="403"
        title="403"
        subTitle="当前会话没有访问此页面所需的实时能力。"
        extra={
          <Button type="primary" href={defaultPathFor(session)}>
            返回首页
          </Button>
        }
      />
    );
  }
  return <>{children}</>;
}
