import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { bootstrapCurrentSession } from '@/api/auth';
import { useAuthStore } from '@/stores/authStore';
import { RequireAuth, CapabilityGuard } from '@/router/guard';
import { appRoutes, defaultPathFor } from '@/router/routes';
import MainLayout from '@/layouts/MainLayout';
import Login from '@/pages/login';
import CustomCursor from '@/components/CustomCursor';

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
      <CustomCursor />
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
          <Route path="*" element={<HomeRedirect />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
