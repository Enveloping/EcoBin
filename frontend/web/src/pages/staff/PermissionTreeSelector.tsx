import { useMemo } from 'react';
import { Button, Empty, Space, Tag, theme, Tree, Typography } from 'antd';
import type { PermissionDefinition, PermissionScopeKind } from '@/types';
import {
  groupPermissionDefinitions,
  normalizePermissionCodes,
  selectAllPermissionCodes,
} from './permissionTree';

interface PermissionTreeSelectorProps {
  definitions: PermissionDefinition[];
  scopeKind: PermissionScopeKind;
  value?: string[];
  onChange?: (permissionCodes: string[]) => void;
  disabled?: boolean;
  delegablePermissionCodes?: readonly string[];
  ariaLabel: string;
  defaultExpandAll?: boolean;
}

export default function PermissionTreeSelector({
  definitions,
  scopeKind,
  value = [],
  onChange,
  disabled = false,
  delegablePermissionCodes,
  ariaLabel,
  defaultExpandAll = true,
}: PermissionTreeSelectorProps) {
  const { token } = theme.useToken();
  const groups = useMemo(
    () => groupPermissionDefinitions(definitions, scopeKind),
    [definitions, scopeKind],
  );
  const leafCodes = useMemo(
    () => groups.flatMap((group) =>
      group.definitions.map((definition) => definition.permissionCode)),
    [groups],
  );
  const availableCodes = useMemo(() => new Set(leafCodes), [leafCodes]);
  const selectedCodes = useMemo(
    () => [...new Set(value)].filter((code) => availableCodes.has(code)),
    [availableCodes, value],
  );
  const selectedSet = useMemo(() => new Set(selectedCodes), [selectedCodes]);
  const delegableSet = useMemo(
    () => delegablePermissionCodes === undefined
      ? null
      : new Set(delegablePermissionCodes),
    [delegablePermissionCodes],
  );

  const treeData = useMemo(
    () => groups.map((group) => {
      const selectedCount = group.definitions.filter((definition) =>
        selectedSet.has(definition.permissionCode)).length;
      return {
        key: `group:${group.key}`,
        title: (
          <Space size={6}>
            <Typography.Text strong>{group.label}</Typography.Text>
            <Typography.Text type="secondary">
              {selectedCount}/{group.definitions.length}
            </Typography.Text>
          </Space>
        ),
        children: group.definitions.map((definition) => {
          const delegable = delegableSet === null
            || delegableSet.has(definition.permissionCode);
          const selected = selectedSet.has(definition.permissionCode);
          return {
            key: definition.permissionCode,
            disableCheckbox: !delegable && !selected,
            title: (
              <Space size={6} wrap>
                <span title={definition.description ?? undefined}>
                  {definition.permissionName}
                </span>
                <Typography.Text code style={{ fontSize: 12 }}>
                  {definition.permissionCode}
                </Typography.Text>
                {!delegable && selected && (
                  <Tag color="warning">仅可移除</Tag>
                )}
              </Space>
            ),
          };
        }),
      };
    }),
    [delegableSet, groups, selectedSet],
  );

  const emit = (codes: Iterable<string>) => {
    onChange?.(normalizePermissionCodes([...codes], definitions, scopeKind));
  };

  const selectAll = () => {
    onChange?.(selectAllPermissionCodes(
      selectedCodes,
      definitions,
      scopeKind,
      delegablePermissionCodes,
    ));
  };

  return (
    <div aria-label={ariaLabel}>
      <Space
        wrap
        style={{
          width: '100%',
          justifyContent: 'space-between',
          marginBottom: 8,
        }}
      >
        <Typography.Text type="secondary">
          已选 {selectedCodes.length} / {leafCodes.length} 项；父级勾选只会提交其叶子权限码。
        </Typography.Text>
        <Space size={4}>
          <Button
            size="small"
            disabled={disabled || leafCodes.length === 0}
            onClick={selectAll}
          >
            全选当前范围
          </Button>
          <Button
            size="small"
            disabled={disabled || selectedCodes.length === 0}
            onClick={() => emit([])}
          >
            清空
          </Button>
        </Space>
      </Space>
      <div
        style={{
          border: `1px solid ${token.colorBorderSecondary}`,
          borderRadius: token.borderRadiusLG,
          maxHeight: 360,
          overflow: 'auto',
          padding: treeData.length ? '8px 4px' : 20,
        }}
      >
        {treeData.length ? (
          <Tree
            aria-label={ariaLabel}
            checkable
            blockNode
            defaultExpandAll={defaultExpandAll}
            disabled={disabled}
            checkedKeys={selectedCodes}
            treeData={treeData}
            onCheck={(checkedKeys) => {
              const keys = Array.isArray(checkedKeys)
                ? checkedKeys
                : checkedKeys.checked;
              emit(keys.map(String).filter((key) => !key.startsWith('group:')));
            }}
          />
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="当前范围没有可配置权限"
          />
        )}
      </div>
    </div>
  );
}
