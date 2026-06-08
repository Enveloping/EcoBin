import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import { RequireAuth, RoleGuard } from '@/router/guard';
import { appRoutes, defaultPathFor } from '@/router/routes';
import MainLayout from '@/layouts/MainLayout';
import Login from '@/pages/login';

function HomeRedirect() {
  const role = useAuthStore((s) => s.role);
  return <Navigate to={defaultPathFor(role)} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
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
          {appRoutes.map((r) => (
            <Route
              key={r.path}
              path={r.path}
              element={<RoleGuard roles={r.roles}>{r.element}</RoleGuard>}
            />
          ))}
          <Route path="*" element={<HomeRedirect />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
