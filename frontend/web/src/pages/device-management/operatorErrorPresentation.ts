import { ApiProblem } from '@/api/request';

/** Keep server diagnostics out of operator-facing toasts and inline alerts. */
export function operatorErrorMessage(
  error: unknown,
  fallback: string,
): string {
  if (!(error instanceof ApiProblem)) return fallback;
  if (error.status === 401) return '登录状态已失效，请重新登录后再试';
  if (error.status === 403) return '当前账号没有执行这项操作的权限';
  if (error.status === 404) return '相关设备记录不存在或已经更新，请刷新页面';
  if (
    error.status === 409
    && (
      error.code.startsWith('DEVICE.CONTROL_BUSY_')
      || error.code.startsWith('DEVICE.ABNORMAL_DELIVERY_')
    )
  ) return error.message;
  if (error.status === 409) return '设备状态已经更新，请刷新页面后按最新状态操作';
  if (error.status === 429) return '操作过于频繁，请稍后再试';
  if (error.status === 0 || error.status >= 500 || error.retryable) {
    return '服务暂时不可用，请稍后再试';
  }
  return fallback;
}
