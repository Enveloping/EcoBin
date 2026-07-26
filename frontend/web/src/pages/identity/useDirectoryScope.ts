import { useEffect, useMemo, useState } from 'react';
import { listIdentityTenants, type DirectoryContext } from '@/api/identityDirectory';
import { useAuthStore } from '@/stores/authStore';

const PLATFORM_TENANT_KEY = 'ecobin.web.target-tenant';

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
  const [tenantCode, setTenantCodeState] = useState<string | undefined>(
    platform ? sessionStorage.getItem(PLATFORM_TENANT_KEY) ?? undefined : undefined,
  );
  const [tenantOptions, setTenantOptions] = useState<
    Array<{ label: string; value: string }>
  >([]);
  const [loading, setLoading] = useState(platform);

  useEffect(() => {
    if (!platform) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    listIdentityTenants({ page: 1, pageSize: 200 })
      .then((page) => {
        if (!active) return;
        const options = page.items.map((tenant) => ({
          label: `${tenant.enterpriseName} · ${tenant.tenantCode}`,
          value: tenant.tenantCode,
        }));
        setTenantOptions(options);
        setTenantCodeState((current) => {
          const selected = options.some((option) => option.value === current)
            ? current
            : options[0]?.value;
          if (selected) sessionStorage.setItem(PLATFORM_TENANT_KEY, selected);
          return selected;
        });
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [platform]);

  const setTenantCode = (value: string) => {
    setTenantCodeState(value);
    sessionStorage.setItem(PLATFORM_TENANT_KEY, value);
  };

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
