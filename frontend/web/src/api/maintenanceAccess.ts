import type { CommandIntent } from './commandIntent';
import request from './request';

export interface MaintenanceSshKey {
  maintenanceSshKeyUid: string;
  label: string;
  publicKey: string;
  fingerprintSha256: string;
  status: 'ACTIVE' | 'REVOKED';
  version: number;
  createdAt: string;
  updatedAt: string;
  revokedAt: string | null;
  revokedReason: string | null;
}

export function listMaintenanceSshKeys(
  options: { silent?: boolean } = {},
) {
  return request<MaintenanceSshKey[]>({
    url: '/api/v1/web/platform/maintenance-ssh-keys',
    method: 'GET',
    noStore: true,
    silent: options.silent,
  });
}

export function createMaintenanceSshKey(
  data: { label: string; publicKey: string },
  intent: CommandIntent,
) {
  return intent.execute<MaintenanceSshKey>({
    url: '/api/v1/web/platform/maintenance-ssh-keys',
    method: 'POST',
    data,
  });
}

export function revokeMaintenanceSshKey(
  keyUid: string,
  data: { expectedVersion: number; reason: string },
  intent: CommandIntent,
) {
  return intent.execute<MaintenanceSshKey>({
    url: `/api/v1/web/platform/maintenance-ssh-keys/${encodeURIComponent(keyUid)}/revocations`,
    method: 'POST',
    data,
  });
}
