/**
 * 高级光标组件：主光标 + 弹簧滞后副光标
 * 主光标小圆点精准跟随；副光标大圆环用弹簧物理滞后跟随。
 * 悬停可交互元素时主光标放大、副光标收缩。
 * 自动隐藏触屏设备；尊重 prefers-reduced-motion（降级为隐藏系统光标但无弹簧跟随）。
 */

import { useEffect, useRef } from 'react';
import { palette, alpha } from '@/theme';

// 可交互元素选择器（含文本输入，保证输入框区域光标反馈）
const INTERACTIVE_SELECTOR =
  'a, button, [role="button"], input, textarea, select, [contenteditable], .ant-btn, .ant-card';

export default function CustomCursor() {
  const dotRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 触屏设备 / 无精确指针设备不启用
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    if (!finePointer) return;

    const dot = dotRef.current;
    const ring = ringRef.current;
    if (!dot || !ring) return;

    // reduced-motion 用户：仅隐藏系统光标，不做弹簧副光标跟随
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    document.body.style.cursor = 'none';

    const mouse = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    const ringPos = { x: mouse.x, y: mouse.y };
    let hovering = false;
    let raf = 0;

    const onMove = (e: MouseEvent) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
      dot.style.transform = `translate(${mouse.x}px, ${mouse.y}px) translate(-50%, -50%)`;

      const target = e.target as HTMLElement;
      hovering = !!target.closest(INTERACTIVE_SELECTOR);
      dot.style.width = hovering ? '14px' : '8px';
      dot.style.height = hovering ? '14px' : '8px';
      ring.style.width = hovering ? '36px' : '40px';
      ring.style.height = hovering ? '36px' : '40px';
      ring.style.borderColor = hovering ? palette.primary : alpha(palette.primaryRGB, 0.5);
    };

    // 弹簧物理跟随（reduced-motion 时副光标直接对齐主光标）
    const stiffness = 0.15;
    const tick = () => {
      if (reducedMotion) {
        ringPos.x = mouse.x;
        ringPos.y = mouse.y;
      } else {
        ringPos.x += (mouse.x - ringPos.x) * stiffness;
        ringPos.y += (mouse.y - ringPos.y) * stiffness;
      }
      ring.style.transform = `translate(${ringPos.x}px, ${ringPos.y}px) translate(-50%, -50%)`;
      raf = requestAnimationFrame(tick);
    };

    const onLeave = () => {
      dot.style.opacity = '0';
      ring.style.opacity = '0';
    };
    const onEnter = () => {
      dot.style.opacity = '1';
      ring.style.opacity = '1';
    };

    window.addEventListener('mousemove', onMove);
    document.addEventListener('mouseleave', onLeave);
    document.addEventListener('mouseenter', onEnter);
    raf = requestAnimationFrame(tick);

    return () => {
      document.body.style.cursor = '';
      window.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseleave', onLeave);
      document.removeEventListener('mouseenter', onEnter);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <>
      <div
        ref={dotRef}
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: palette.primary,
          pointerEvents: 'none',
          zIndex: 99999,
          transition: 'width 0.2s, height 0.2s, opacity 0.2s',
          opacity: 0,
        }}
      />
      <div
        ref={ringRef}
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: 40,
          height: 40,
          borderRadius: '50%',
          border: `1.5px solid ${alpha(palette.primaryRGB, 0.5)}`,
          pointerEvents: 'none',
          zIndex: 99998,
          transition: 'width 0.25s, height 0.25s, border-color 0.25s, opacity 0.2s',
          opacity: 0,
        }}
      />
    </>
  );
}