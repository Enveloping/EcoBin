import type { CommandIntent } from './commandIntent';
import request from './request';

export type RemoteSupportState =
  | 'PREPARING'
  | 'CONNECTING'
  | 'OPEN'
  | 'RECONNECTING'
  | 'CLOSING'
  | 'CLOSED'
  | 'FAILED'
  | 'EXPIRED';

export interface RemoteSupportSession {
  sessionUid: string;
  hardwareSn: string;
  maintenanceSshKeyUid: string;
  state: RemoteSupportState;
  remotePort: number;
  connectDeadlineAt: string;
  expiresAt: string;
  openedAt: string | null;
  closedAt: string | null;
  leaseReleasedAt: string | null;
  leaseCleanupPending: boolean;
  failureCode: string | null;
  certificate: string | null;
  bastionHost: string;
  bastionSshPort: number;
  bastionUser: string;
  targetUser: string;
  hostKeyAlias: string;
  knownHostsLine: string;
  sshCommand: string;
  version: number;
}

export function openRemoteSupportSession(
  hardwareSn: string,
  data: {
    maintenanceSshKeyUid: string;
    lifetimeSeconds: number;
    reason: string;
  },
  intent: CommandIntent,
) {
  return intent.execute<RemoteSupportSession>({
    url: `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}/remote-support-sessions`,
    method: 'POST',
    data,
    noStore: true,
  });
}

export function getRemoteSupportSession(sessionUid: string) {
  return request<RemoteSupportSession>({
    url: `/api/v1/web/platform/remote-support-sessions/${encodeURIComponent(sessionUid)}`,
    method: 'GET',
    noStore: true,
    silent: true,
  });
}

export function getCurrentRemoteSupportSession(hardwareSn: string) {
  return request<RemoteSupportSession>({
    url: `/api/v1/web/platform/device-assets/${encodeURIComponent(hardwareSn)}/remote-support-sessions/current`,
    method: 'GET',
    noStore: true,
    silent: true,
  });
}

export function closeRemoteSupportSession(
  sessionUid: string,
  data: { reason: string },
  intent: CommandIntent,
) {
  return intent.execute<RemoteSupportSession>({
    url: `/api/v1/web/platform/remote-support-sessions/${encodeURIComponent(sessionUid)}/closures`,
    method: 'POST',
    data,
    noStore: true,
  });
}
