import { Tag } from 'antd';
import type { EnumMap } from '@/constants';

interface Props {
  map: EnumMap;
  value: number | null | undefined;
}

/** 按枚举映射渲染带颜色的 Tag；未知值原样展示 */
export default function EnumTag({ map, value }: Props) {
  if (value === null || value === undefined) return <span>-</span>;
  const item = map[value];
  if (!item) return <Tag>{value}</Tag>;
  return <Tag color={item.color}>{item.label}</Tag>;
}
