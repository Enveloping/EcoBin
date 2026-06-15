/**
 * 鼠标跟随光晕组件
 * 一个跟随鼠标移动的径向柔光，营造高级感。
 * 挂载到任意容器，光晕会铺满该容器并跟随鼠标。
 */

import { useEffect, useRef } from 'react';
import { palette } from '@/theme';

interface Props {
  /** 光晕颜色（rgba 三元组字符串） */
  color?: string;
  /** 光晕半径（px） */
  size?: number;
  /** 透明度 */
  intensity?: number;
}

export default function MouseSpotlight({
  color = palette.primaryRGB,
  size = 400,
  intensity = 0.08,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const parent = ref.current?.parentElement;
    if (!parent) return;

    const handleMove = (e: MouseEvent) => {
      const rect = parent.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      if (ref.current) {
        ref.current.style.background = `radial-gradient(${size}px circle at ${x}px ${y}px, rgba(${color}, ${intensity}), transparent 70%)`;
      }
    };

    parent.addEventListener('mousemove', handleMove);
    return () => parent.removeEventListener('mousemove', handleMove);
  }, [color, size, intensity]);

  return (
    <div
      ref={ref}
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        zIndex: 0,
        transition: 'background 0.2s ease-out',
      }}
    />
  );
}