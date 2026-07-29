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
  const bootstrapIssue = useAuthStore((state) => state.bootstrapIssue);
  const location = useLocation();
  if (status === 'checking') {
    return (
      <div className="route-loading" aria-label="正在载入会话">
        <Skeleton active paragraph={{ rows: 4 }} />
      </div>
    );
  }
  if (status === 'unavailable') {
    const requestId = bootstrapIssue?.requestId
      ? `请求编号：${bootstrapIssue.requestId}`
      : undefined;
    return (
      <Result
        status="500"
        title="暂时无法确认登录状态"
        subTitle={[bootstrapIssue?.message, requestId]
          .filter(Boolean)
          .join('；')}
        extra={
          <Button type="primary" onClick={() => window.location.reload()}>
            重新检查
          </Button>
        }
      />
    );
  }
  if (status !== 'authenticated') {
    const from = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to="/login" replace state={{ from }} />;
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
