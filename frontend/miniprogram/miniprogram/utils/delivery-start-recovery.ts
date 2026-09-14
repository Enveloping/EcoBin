export type RejectedDeliveryStartDisposition = 'RELEASE' | 'RETAIN'

// 409 可能来自同一幂等键的并发请求，不能据此断定服务端未受理。
// 只有这些响应能明确证明当前启动请求没有触发设备动作。
const DEFINITE_REJECTION_STATUS = new Set([400, 401, 403, 404, 422])

export function rejectedDeliveryStartDisposition(
  status: number,
): RejectedDeliveryStartDisposition {
  return DEFINITE_REJECTION_STATUS.has(status) ? 'RELEASE' : 'RETAIN'
}
