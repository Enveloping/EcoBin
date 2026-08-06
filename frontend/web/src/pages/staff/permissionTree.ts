export interface PermissionDefinitionLike {
  permissionCode: string;
  scopeKind: 'TENANT' | 'ORGANIZATION';
  permissionName: string;
  description?: string | null;
}

export interface PermissionGroup {
  key: string;
  label: string;
  definitions: PermissionDefinitionLike[];
}

interface PermissionGroupRule {
  key: string;
  label: string;
  prefixes: string[];
}

export const PERMISSION_GROUP_RULES: readonly PermissionGroupRule[] = [
  {
    key: 'tenant-organization',
    label: '租户与机构',
    prefixes: ['tenant.', 'organization.', 'organization-manager.'],
  },
  {
    key: 'miniapp',
    label: '小程序配置',
    prefixes: ['miniapp.'],
  },
  {
    key: 'staff-access',
    label: '工作人员与授权',
    prefixes: ['staff.', 'permission.'],
  },
  {
    key: 'organization-user',
    label: '机构用户',
    prefixes: ['user.'],
  },
  {
    key: 'device',
    label: '设备',
    prefixes: ['device.'],
  },
  {
    key: 'delivery-review',
    label: '投递与审核',
    prefixes: ['delivery.', 'review.'],
  },
  {
    key: 'clean',
    label: '清运',
    prefixes: ['clean.', 'cleaner.'],
  },
  {
    key: 'funds',
    label: '钱包与资金',
    prefixes: [
      'wallet.',
      'fund.',
      'funds.',
      'recharge.',
      'withdrawal.',
    ],
  },
  {
    key: 'operations',
    label: '运营治理',
    prefixes: [
      'audit.',
      'alert.',
      'reconciliation.',
      'statistics.',
    ],
  },
] as const;

export function permissionGroupKey(permissionCode: string): string {
  return PERMISSION_GROUP_RULES.find((rule) =>
    rule.prefixes.some((prefix) => permissionCode.startsWith(prefix)))?.key
    ?? 'other';
}

export function groupPermissionDefinitions(
  definitions: readonly PermissionDefinitionLike[],
  scopeKind: PermissionDefinitionLike['scopeKind'],
): PermissionGroup[] {
  const byGroup = new Map<string, PermissionDefinitionLike[]>();
  const uniqueCodes = new Set<string>();

  definitions
    .filter((definition) => definition.scopeKind === scopeKind)
    .forEach((definition) => {
      if (uniqueCodes.has(definition.permissionCode)) return;
      uniqueCodes.add(definition.permissionCode);
      const groupKey = permissionGroupKey(definition.permissionCode);
      const current = byGroup.get(groupKey) ?? [];
      current.push(definition);
      byGroup.set(groupKey, current);
    });

  const configuredGroups = PERMISSION_GROUP_RULES.flatMap((rule) => {
    const groupDefinitions = byGroup.get(rule.key);
    if (!groupDefinitions?.length) return [];
    return [{
      key: rule.key,
      label: rule.label,
      definitions: [...groupDefinitions].sort((left, right) =>
        left.permissionCode.localeCompare(right.permissionCode)),
    }];
  });
  const otherDefinitions = byGroup.get('other');
  return otherDefinitions?.length
    ? [
        ...configuredGroups,
        {
          key: 'other',
          label: '其他',
          definitions: [...otherDefinitions].sort((left, right) =>
            left.permissionCode.localeCompare(right.permissionCode)),
        },
      ]
    : configuredGroups;
}

export function normalizePermissionCodes(
  permissionCodes: readonly string[],
  definitions: readonly PermissionDefinitionLike[],
  scopeKind: PermissionDefinitionLike['scopeKind'],
): string[] {
  const availableCodes = new Set(
    definitions
      .filter((definition) => definition.scopeKind === scopeKind)
      .map((definition) => definition.permissionCode),
  );
  return [...new Set(permissionCodes)]
    .filter((permissionCode) => availableCodes.has(permissionCode))
    .sort((left, right) => left.localeCompare(right));
}

export function selectAllPermissionCodes(
  currentPermissionCodes: readonly string[],
  definitions: readonly PermissionDefinitionLike[],
  scopeKind: PermissionDefinitionLike['scopeKind'],
  delegablePermissionCodes?: readonly string[],
): string[] {
  const delegable = delegablePermissionCodes === undefined
    ? null
    : new Set(delegablePermissionCodes);
  const additions = definitions
    .filter((definition) =>
      definition.scopeKind === scopeKind
      && (delegable === null || delegable.has(definition.permissionCode)))
    .map((definition) => definition.permissionCode);
  return normalizePermissionCodes(
    [...currentPermissionCodes, ...additions],
    definitions,
    scopeKind,
  );
}
