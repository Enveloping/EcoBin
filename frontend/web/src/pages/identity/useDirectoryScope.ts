import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { message } from 'antd';
import {
  listAllIdentityTenants,
  type DirectoryContext,
} from '@/api/identityDirectory';
import { useAuthStore } from '@/stores/authStore';

const PLATFORM_TENANT_KEY = 'ecobin.web.target-tenant';
const TENANT_QUERY_KEY = 'tenant';

export interface DirectoryScope {
  context: DirectoryContext | null;
  platform: boolean;
  tenantCode?: string;
  tenantOptions: Array<{ label: string; value: string }>;
  loading: boolean;
  setTenantCode: (tenantCode: string) => void;
}

export function useDirectoryScope(): DirectoryScope {
  const domain = useAuthStore((state) => state.domain);
  const sessionTenant = useAuthStore((state) => state.session?.tenantCode);
  const platform = domain === 'platform';
  const [searchParams, setSearchParams] = useSearchParams();
  const queryTenant = searchParams.get(TENANT_QUERY_KEY)?.trim() || undefined;
  const [tenantOptions, setTenantOptions] = useState<
    Array<{ label: string; value: string }>
  >([]);
  const [loading, setLoading] = useState(platform);

  const writeTenantQuery = useCallback(
    (tenantCode: string) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set(TENANT_QUERY_KEY, tenantCode);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  useEffect(() => {
    if (!platform) {
      setTenantOptions([]);
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    listAllIdentityTenants({}, { silent: true })
      .then((tenants) => {
        if (!active) return;
        setTenantOptions(
          tenants.map((tenant) => ({
            label: `${tenant.enterpriseName} · ${tenant.tenantCode}`,
            value: tenant.tenantCode,
          })),
        );
      })
      .catch(() => {
        if (!active) return;
        setTenantOptions([]);
        message.error('租户列表暂时无法读取，请刷新页面后再试');
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [platform]);

  useEffect(() => {
    if (!platform || loading || !tenantOptions.length) return;
    const queryIsValid = tenantOptions.some(
      (option) => option.value === queryTenant,
    );
    if (queryIsValid && queryTenant) {
      sessionStorage.setItem(PLATFORM_TENANT_KEY, queryTenant);
      return;
    }
    const remembered = sessionStorage.getItem(PLATFORM_TENANT_KEY);
    const selected = tenantOptions.some((option) => option.value === remembered)
      ? remembered!
      : tenantOptions[0].value;
    sessionStorage.setItem(PLATFORM_TENANT_KEY, selected);
    writeTenantQuery(selected);
  }, [
    loading,
    platform,
    queryTenant,
    tenantOptions,
    writeTenantQuery,
  ]);

  const tenantCode = platform
    && tenantOptions.some((option) => option.value === queryTenant)
    ? queryTenant
    : undefined;

  const setTenantCode = useCallback(
    (value: string) => {
      sessionStorage.setItem(PLATFORM_TENANT_KEY, value);
      writeTenantQuery(value);
    },
    [writeTenantQuery],
  );

  const context = useMemo<DirectoryContext | null>(() => {
    if (!domain) return null;
    if (domain === 'platform') {
      return tenantCode ? { domain, tenantCode } : null;
    }
    return sessionTenant ? { domain, tenantCode: sessionTenant } : { domain };
  }, [domain, sessionTenant, tenantCode]);

  return {
    context,
    platform,
    tenantCode,
    tenantOptions,
    loading,
    setTenantCode,
  };
}
