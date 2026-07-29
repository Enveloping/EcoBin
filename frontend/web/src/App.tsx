import { lazy, Suspense, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Skeleton } from 'antd';
import { bootstrapCurrentSession } from '@/api/auth';
import { ApiProblem } from '@/api/request';
import { useAuthStore } from '@/stores/authStore';
import { RequireAuth, CapabilityGuard } from '@/router/guard';
import { appRoutes, defaultPathFor } from '@/router/routes';
import Login from '@/pages/login';

const MainLayout = lazy(() => import('@/layouts/MainLayout'));
const NotFoundPage = lazy(() => import('@/pages/NotFound'));

function RouteFallback() {
  return (
    <div className="route-loading" aria-label="正在载入页面">
      <Skeleton active paragraph={{ rows: 6 }} />
    </div>
  );
}

function HomeRedirect() {
  const session = useAuthStore((state) => state.session);
  return <Navigate to={defaultPathFor(session)} replace />;
}

export default function App() {
  const setSession = useAuthStore((state) => state.setSession);
  const setChecking = useAuthStore((state) => state.setChecking);
  const setUnavailable = useAuthStore((state) => state.setUnavailable);
  const clear = useAuthStore((state) => state.clear);

  useEffect(() => {
    let active = true;
    setChecking();
    bootstrapCurrentSession()
      .then(({ session, domain }) => {
        if (
          active
          && useAuthStore.getState().status === 'checking'
        ) {
          setSession(session, domain);
        }
      })
      .catch((error: unknown) => {
        if (
          !active
          || useAuthStore.getState().status !== 'checking'
        ) {
          return;
        }
        if (error instanceof ApiProblem && error.status === 401) {
          clear();
          return;
        }
        setUnavailable({
          message: error instanceof Error
            ? error.message
            : '暂时无法连接认证服务',
          requestId: error instanceof ApiProblem && error.requestId
            ? error.requestId
            : undefined,
        });
      });
    return () => {
      active = false;
    };
  }, [clear, setChecking, setSession, setUnavailable]);

  return (
    <BrowserRouter>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <RequireAuth>
                <MainLayout />
              </RequireAuth>
            }
          >
            <Route index element={<HomeRedirect />} />
            {appRoutes.map((route) => (
              <Route
                key={route.path}
                path={route.path}
                element={
                  <CapabilityGuard route={route}>
                    {route.element}
                  </CapabilityGuard>
                }
              />
            ))}
            <Route path="/not-found" element={<NotFoundPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
