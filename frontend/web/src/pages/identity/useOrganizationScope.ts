import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { listAllOrganizations } from '@/api/identityDirectory';
import type { DirectoryScope } from './useDirectoryScope';

const ORGANIZATION_QUERY_KEY = 'organization';

export interface OrganizationScope {
  organizationCode?: string;
  organizationOptions: Array<{ label: string; value: string }>;
  loading: boolean;
  setOrganizationCode: (organizationCode: string) => void;
}

/**
 * Keeps an organization selection in the URL so table links remain shareable.
 * The server still validates tenant and organization scope on every request.
 */
export function useOrganizationScope(
  directoryScope: DirectoryScope,
): OrganizationScope {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedOrganization =
    searchParams.get(ORGANIZATION_QUERY_KEY)?.trim() || undefined;
  const [organizationOptions, setOrganizationOptions] = useState<
    Array<{ label: string; value: string }>
  >([]);
  const [loading, setLoading] = useState(false);

  const writeOrganizationQuery = useCallback(
    (organizationCode: string) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set(ORGANIZATION_QUERY_KEY, organizationCode);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  useEffect(() => {
    setOrganizationOptions([]);
    if (!directoryScope.context) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    listAllOrganizations(directoryScope.context)
      .then((organizations) => {
        if (!active) return;
        setOrganizationOptions(
          organizations.map((organization) => ({
            label:
              `${organization.organizationName} · `
              + organization.organizationCode,
            value: organization.organizationCode,
          })),
        );
      })
      .catch(() => {
        if (active) setOrganizationOptions([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [directoryScope.context]);

  useEffect(() => {
    if (loading || !organizationOptions.length) return;
    if (
      requestedOrganization
      && organizationOptions.some(
        (option) => option.value === requestedOrganization,
      )
    ) {
      return;
    }
    writeOrganizationQuery(organizationOptions[0].value);
  }, [
    loading,
    organizationOptions,
    requestedOrganization,
    writeOrganizationQuery,
  ]);

  const organizationCode = organizationOptions.some(
    (option) => option.value === requestedOrganization,
  )
    ? requestedOrganization
    : undefined;

  return {
    organizationCode,
    organizationOptions,
    loading,
    setOrganizationCode: writeOrganizationQuery,
  };
}
