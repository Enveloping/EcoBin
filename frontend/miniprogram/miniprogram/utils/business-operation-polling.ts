const POLL_INTERVAL_MS = 5 * 1000

/**
 * 投递、清运受理后的查询节奏。
 *
 * 自动查询始终每 5 秒执行一次。用户下拉刷新不经过这里，
 * 会立即读取一次状态；如果已有同一状态请求在途，则等待并复用该请求。
 */
export function businessOperationPollDelay(): number {
  return POLL_INTERVAL_MS
}
