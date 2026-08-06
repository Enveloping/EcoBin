import assert from 'node:assert/strict';
import test from 'node:test';
import {
  groupPermissionDefinitions,
  normalizePermissionCodes,
  permissionGroupKey,
  selectAllPermissionCodes,
} from '../web/src/pages/staff/permissionTree.ts';

const definitions = [
  {
    permissionCode: 'device.read',
    permissionName: '读取设备',
    scopeKind: 'TENANT',
  },
  {
    permissionCode: 'device.manage',
    permissionName: '管理设备',
    scopeKind: 'TENANT',
  },
  {
    permissionCode: 'wallet.read',
    permissionName: '读取钱包',
    scopeKind: 'TENANT',
  },
  {
    permissionCode: 'device.read',
    permissionName: '读取机构设备',
    scopeKind: 'ORGANIZATION',
  },
  {
    permissionCode: 'cleaner.manage',
    permissionName: '管理清运员',
    scopeKind: 'ORGANIZATION',
  },
];

test('permission definitions are grouped by business prefix within one scope', () => {
  assert.equal(permissionGroupKey('device.read'), 'device');
  assert.equal(permissionGroupKey('cleaner.manage'), 'clean');
  assert.equal(permissionGroupKey('future.capability'), 'other');

  const tenantGroups = groupPermissionDefinitions(definitions, 'TENANT');
  assert.deepEqual(
    tenantGroups.map((group) => [
      group.label,
      group.definitions.map((definition) => definition.permissionCode),
    ]),
    [
      ['设备', ['device.manage', 'device.read']],
      ['钱包与资金', ['wallet.read']],
    ],
  );
});

test('tree keys normalize to sorted leaf codes and never submit group keys', () => {
  assert.deepEqual(
    normalizePermissionCodes(
      ['group:device', 'wallet.read', 'device.read', 'device.read'],
      definitions,
      'TENANT',
    ),
    ['device.read', 'wallet.read'],
  );
});

test('select all is scope-local and only adds permissions the operator can delegate', () => {
  assert.deepEqual(
    selectAllPermissionCodes(
      ['wallet.read'],
      definitions,
      'TENANT',
      ['device.read'],
    ),
    ['device.read', 'wallet.read'],
  );
  assert.deepEqual(
    selectAllPermissionCodes([], definitions, 'ORGANIZATION'),
    ['cleaner.manage', 'device.read'],
  );
});
