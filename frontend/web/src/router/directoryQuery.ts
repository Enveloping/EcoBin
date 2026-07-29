export interface DirectoryLinkScope {
  tenant?: string | null;
  organization?: string | null;
  organizationUserUid?: string | null;
  view?: string | null;
}

export function directoryPath(
  pathname: string,
  scope: DirectoryLinkScope = {},
): string {
  const query = new URLSearchParams();
  if (scope.tenant) query.set('tenant', scope.tenant);
  if (scope.organization) {
    query.set('organization', scope.organization);
  }
  if (scope.organizationUserUid) {
    query.set('organizationUserUid', scope.organizationUserUid);
  }
  if (scope.view && scope.view !== 'all') query.set('view', scope.view);
  const search = query.toString();
  return search ? `${pathname}?${search}` : pathname;
}

/**
 * Menu targets may already carry a view preset. Preserve only the platform
 * tenant context from the current URL and never copy arbitrary filters.
 */
export function menuTargetPath(
  target: string,
  currentSearch: string,
  preserveTenant: boolean,
): string {
  const separator = target.indexOf('?');
  const pathname = separator >= 0 ? target.slice(0, separator) : target;
  const query = new URLSearchParams(
    separator >= 0 ? target.slice(separator + 1) : '',
  );
  if (preserveTenant && !query.has('tenant')) {
    const tenant = new URLSearchParams(currentSearch).get('tenant')?.trim();
    if (tenant) query.set('tenant', tenant);
  }
  const search = query.toString();
  return search ? `${pathname}?${search}` : pathname;
}
