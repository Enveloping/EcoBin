const INITIAL_POLL_WINDOW_MS = 120 * 1000
const INITIAL_POLL_INTERVAL_MS = 5 * 1000
const FOLLOW_UP_POLL_INTERVAL_MS = 3 * 1000

/**
 * 投递、清运受理后的查询节奏。
 *
 * 以服务端受理成功的本地时刻为起点：前 120 秒每 5 秒查询一次，
 * 从第 120 秒开始每 3 秒查询一次。页面隐藏再恢复时仍沿用原起点。
 */
export function businessOperationPollDelay(
  acceptedAtMs: number,
  nowMs = Date.now(),
): number {
  const elapsedMs = Number.isFinite(acceptedAtMs)
    && Number.isFinite(nowMs)
    ? Math.max(0, nowMs - acceptedAtMs)
    : 0
  return elapsedMs < INITIAL_POLL_WINDOW_MS
    ? INITIAL_POLL_INTERVAL_MS
    : FOLLOW_UP_POLL_INTERVAL_MS
}
