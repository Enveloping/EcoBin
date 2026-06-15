/**
 * 3D 倾斜交互 Hook
 * 元素根据鼠标在内部的位置做透视倾斜 + 高光偏移。
 * 直接操作 DOM（e.currentTarget.style），不触发 React 重渲染，
 * 鼠标高频移动也零开销。返回事件处理器直接挂到元素。
 */

import { useCallback } from 'react';

interface TiltHandlers {
  onMouseMove: (e: React.MouseEvent<HTMLElement>) => void;
  onMouseLeave: (e: React.MouseEvent<HTMLElement>) => void;
}

export function useTilt(maxTilt = 10): TiltHandlers {
  const onMouseMove = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      const el = e.currentTarget;
      const rect = el.getBoundingClientRect();
      const px = (e.clientX - rect.left) / rect.width; // 0~1
      const py = (e.clientY - rect.top) / rect.height; // 0~1
      const rotateX = (0.5 - py) * maxTilt * 2;
      const rotateY = (px - 0.5) * maxTilt * 2;
      el.style.transform = `perspective(800px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale(1.03)`;
      el.style.backgroundImage = `radial-gradient(circle at ${px * 100}% ${py * 100}%, rgba(255,255,255,0.25), transparent 60%)`;
    },
    [maxTilt],
  );

  const onMouseLeave = useCallback((e: React.MouseEvent<HTMLElement>) => {
    const el = e.currentTarget;
    el.style.transform = 'perspective(800px) rotateX(0deg) rotateY(0deg) scale(1)';
    el.style.backgroundImage = '';
  }, []);

  return { onMouseMove, onMouseLeave };
}
