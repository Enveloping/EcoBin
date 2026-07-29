import { useCallback, useRef } from 'react';
import { createCommandIntent, type CommandIntent } from '@/api/commandIntent';
import { ApiProblem } from '@/api/request';

interface PendingIntent {
  key: string;
  intent: CommandIntent;
}

/**
 * Keeps one idempotency key alive across retryable failures of the same user
 * intent. A changed key means the form payload or target changed and starts a
 * new intent. Successful and terminal failures always clear the slot.
 */
export function useCommandExecutor() {
  const pendingRef = useRef<PendingIntent | null>(null);

  return useCallback(
    async <T>(
      key: string,
      operation: (intent: CommandIntent) => Promise<T>,
    ): Promise<T> => {
      const pending = pendingRef.current?.key === key
        ? pendingRef.current
        : { key, intent: createCommandIntent() };
      pendingRef.current = pending;

      try {
        const result = await operation(pending.intent);
        if (pendingRef.current === pending) pendingRef.current = null;
        return result;
      } catch (error) {
        if (
          pendingRef.current === pending
          && (!(error instanceof ApiProblem) || !error.retryable)
        ) {
          pendingRef.current = null;
        }
        throw error;
      }
    },
    [],
  );
}

export function commandKey(
  operation: string,
  target: string,
  payload: unknown,
): string {
  const source = JSON.stringify(payload);
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${operation}:${target}:${(hash >>> 0).toString(16)}`;
}
