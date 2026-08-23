import type { CommandIntent } from './commandIntent';
import type { components, operations } from './generated/openapi';
import request from './request';

type Schemas = components['schemas'];

export type ReliableTask = Schemas['ReliableTask'];
export type ReliableTaskPage = Schemas['ReliableTaskPage'];
export type ReliableTaskAttempt = Schemas['TaskAttempt'];
export type ReliableTaskAttemptPage = Schemas['TaskAttemptCursorPage'];
export type ResumeReliableTaskRequest = Schemas['ResumeTaskRequest'];
export type ReliableTaskResumption = Schemas['ReliableTaskResumption'];

export type ReliableTaskListParams = NonNullable<
  operations['listReliableTasks']['parameters']['query']
>;
export type ReliableTaskAttemptListParams = NonNullable<
  operations['listReliableTaskAttempts']['parameters']['query']
>;

const base = '/api/v1/web/platform/operations/reliable-tasks';

function taskPath(taskUid: string) {
  return `${base}/${encodeURIComponent(taskUid)}`;
}

export function listReliableTasks(
  params: ReliableTaskListParams = {},
) {
  return request<ReliableTaskPage>({
    url: base,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getReliableTask(taskUid: string) {
  return request<ReliableTask>({
    url: taskPath(taskUid),
    method: 'GET',
    noStore: true,
  });
}

export function listReliableTaskAttempts(
  taskUid: string,
  params: ReliableTaskAttemptListParams = {},
) {
  return request<ReliableTaskAttemptPage>({
    url: `${taskPath(taskUid)}/attempts`,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function resumeReliableTask(
  taskUid: string,
  data: ResumeReliableTaskRequest,
  intent: CommandIntent,
) {
  return intent.executeAccepted<
    ReliableTaskResumption,
    ResumeReliableTaskRequest
  >({
    url: `${taskPath(taskUid)}/resumptions`,
    method: 'POST',
    data,
  });
}
