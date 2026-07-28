import { lazy, Suspense, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Skeleton } from 'antd';
import { bootstrapCurrentSession } from '@/api/auth';
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
  const clear = useAuthStore((state) => state.clear);

  useEffect(() => {
    let active = true;
    bootstrapCurrentSession()
      .then(({ session, domain }) => {
        if (active) setSession(session, domain);
      })
      .catch(() => {
        if (active) clear();
      });
    return () => {
      active = false;
    };
  }, [clear, setSession]);

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
