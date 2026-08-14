import request from './request';
import type { CommandIntent } from './commandIntent';
import type { PageData, PlatformAdmin } from '@/types';

const BASE = '/api/v1/web/platform/admin-accounts';

export interface PlatformAdminPageParams {
  page?: number;
  pageSize?: number;
  status?: PlatformAdmin['status'];
  query?: string;
}

export interface PlatformPasswordChangeInput {
  currentPassword: string;
  newPassword: string;
  expectedVersion: number;
  expectedAuthVersion: number;
}

function write(intent: CommandIntent, url: string, data: unknown) {
  return intent.execute<PlatformAdmin>({ url, method: 'POST', data });
}

export function listPlatformAdministrators(
  params: PlatformAdminPageParams = {},
) {
  return request<PageData<PlatformAdmin>>({
    url: BASE,
    method: 'GET',
    params,
  });
}

export function getPlatformAdministrator(platformAdminUid: string) {
  return request<PlatformAdmin>({
    url: `${BASE}/${encodeURIComponent(platformAdminUid)}`,
    method: 'GET',
  });
}

export function createPlatformAdministrator(
  data: {
    loginName: string;
    initialPassword: string;
    displayName: string;
  },
  intent: CommandIntent,
) {
  return write(intent, BASE, data);
}

export function changePlatformAdministratorStatus(
  administrator: PlatformAdmin,
  enabled: boolean,
  reason: string,
  intent: CommandIntent,
) {
  const action = enabled ? 'activations' : 'deactivations';
  return write(
    intent,
    `${BASE}/${encodeURIComponent(administrator.platformAdminUid)}/${action}`,
    {
      expectedVersion: administrator.version,
      expectedAuthVersion: administrator.authVersion,
      reason,
    },
  );
}

export function resetPlatformAdministratorPassword(
  administrator: PlatformAdmin,
  newPassword: string,
  intent: CommandIntent,
) {
  return write(
    intent,
    `${BASE}/${encodeURIComponent(administrator.platformAdminUid)}/password-resets`,
    {
      newPassword,
      expectedVersion: administrator.version,
      expectedAuthVersion: administrator.authVersion,
    },
  );
}

export function deletePlatformAdministrator(
  administrator: PlatformAdmin,
  reason: string,
  intent: CommandIntent,
) {
  return write(
    intent,
    `${BASE}/${encodeURIComponent(administrator.platformAdminUid)}/deletions`,
    {
      expectedVersion: administrator.version,
      expectedAuthVersion: administrator.authVersion,
      reason,
    },
  );
}

export function changeCurrentPlatformAdministratorPassword(
  data: PlatformPasswordChangeInput,
  intent: CommandIntent,
) {
  return write(intent, `${BASE}/current/password-changes`, data);
}
