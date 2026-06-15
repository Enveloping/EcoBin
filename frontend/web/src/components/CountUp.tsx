/**
 * 数字滚动显示组件
 * 数值用 useCountUp 做缓动滚动；非数值（字符串/未定义）原样显示。
 * 支持小数位数 precision。
 */

import { useCountUp } from '@/hooks/useCountUp';

interface Props {
  value: number | string | undefined | null;
  precision?: number;
  duration?: number;
}

export default function CountUp({ value, precision = 0, duration }: Props) {
  const num = Number(value);
  const animated = useCountUp(num, duration);

  if (value === null || value === undefined || Number.isNaN(num)) {
    return <>{value ?? '-'}</>;
  }
  if (!Number.isFinite(num)) {
    return <>{String(value)}</>;
  }

  return <>{animated.toLocaleString('zh-CN', { maximumFractionDigits: precision })}</>;
}
