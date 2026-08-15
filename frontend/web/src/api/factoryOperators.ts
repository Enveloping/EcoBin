import type { CommandIntent } from './commandIntent';
import request from './request';
import type { PageData } from '@/types';

const BASE = '/api/v1/web/platform/factory-operators';

export type FactoryOperatorStatus = 'ACTIVE' | 'DISABLED';
export type FactoryBindingStatus = 'ACTIVE' | 'UNBOUND';

export interface FactoryOperator {
  factoryOperatorUid: string;
  operatorCode: string;
  displayName: string;
  status: FactoryOperatorStatus;
  version: number;
  authVersion: number;
  bindingStatus: FactoryBindingStatus;
  bindingUid: string | null;
  boundAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface FactoryBindingIntent {
  bindingIntentUid: string;
  expiresAt: string;
  miniProgramCodeDataUrl: string;
}

export interface FactoryOperatorPageParams {
  page?: number;
  pageSize?: number;
  status?: FactoryOperatorStatus;
  query?: string;
}

function command(
  intent: CommandIntent,
  url: string,
  data: unknown,
) {
  return intent.execute<FactoryOperator>({ url, method: 'POST', data });
}

export function listFactoryOperators(
  params: FactoryOperatorPageParams = {},
) {
  return request<PageData<FactoryOperator>>({
    url: BASE,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function createFactoryOperator(
  data: { operatorCode: string; displayName: string },
  intent: CommandIntent,
) {
  return intent.execute<FactoryOperator>({
    url: BASE,
    method: 'POST',
    data,
  });
}

export function updateFactoryOperator(
  operator: FactoryOperator,
  displayName: string,
  intent: CommandIntent,
) {
  return intent.execute<FactoryOperator>({
    url: `${BASE}/${encodeURIComponent(operator.factoryOperatorUid)}`,
    method: 'PUT',
    data: {
      expectedVersion: operator.version,
      displayName,
    },
  });
}

export function changeFactoryOperatorStatus(
  operator: FactoryOperator,
  enabled: boolean,
  reason: string,
  intent: CommandIntent,
) {
  const action = enabled ? 'activations' : 'deactivations';
  return command(
    intent,
    `${BASE}/${encodeURIComponent(operator.factoryOperatorUid)}/${action}`,
    { expectedVersion: operator.version, reason },
  );
}

export function createFactoryBindingIntent(operator: FactoryOperator) {
  return request<FactoryBindingIntent>({
    url: `${BASE}/${encodeURIComponent(operator.factoryOperatorUid)}/miniapp-binding-intents`,
    method: 'POST',
    noStore: true,
  });
}

export function revokeFactoryBinding(
  operator: FactoryOperator,
  reason: string,
  intent: CommandIntent,
) {
  return command(
    intent,
    `${BASE}/${encodeURIComponent(operator.factoryOperatorUid)}/miniapp-binding-revocations`,
    { expectedVersion: operator.version, reason },
  );
}
