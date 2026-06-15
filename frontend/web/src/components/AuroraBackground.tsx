/**
 * 极光渐变背景组件
 * 多层彩色光斑缓慢流动，模拟极光效果。纯 CSS 动画，无 JS 开销。
 * 适合作为 Dashboard / 大屏页面的底层背景。
 */

import { palette } from '@/theme';

interface Blob {
  color: string;
  pos: { top?: string; bottom?: string; left?: string; right?: string };
  size: number;
  delay: string;
}

interface Props {
  variant?: 'teal' | 'mixed';
  opacity?: number;
}

export default function AuroraBackground({ variant = 'teal', opacity = 0.6 }: Props) {
  const blobs: Blob[] =
    variant === 'mixed'
      ? [
          { color: palette.primary, pos: { top: '-10%', left: '-5%' }, size: 480, delay: '0s' },
          { color: '#0EA5E9', pos: { top: '30%', right: '-10%' }, size: 420, delay: '-6s' },
          { color: palette.primaryLight, pos: { bottom: '-15%', left: '20%' }, size: 460, delay: '-12s' },
        ]
      : [
          { color: palette.primary, pos: { top: '-10%', left: '-5%' }, size: 480, delay: '0s' },
          { color: palette.primarySoft, pos: { top: '20%', right: '-8%' }, size: 420, delay: '-7s' },
          { color: palette.primaryLight, pos: { bottom: '-15%', left: '25%' }, size: 460, delay: '-14s' },
        ];

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
        opacity,
        pointerEvents: 'none',
        zIndex: 0,
      }}
    >
      {blobs.map((b, i) => (
        <div
          key={`aurora-${i}`}
          style={{
            position: 'absolute',
            ...b.pos,
            width: b.size,
            height: b.size,
            borderRadius: '50%',
            background: b.color,
            filter: 'blur(80px)',
            animation: `aurora-float-${i} 20s ease-in-out infinite`,
            animationDelay: b.delay,
          }}
        />
      ))}
      <style>{`
        @keyframes aurora-float-0 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          33% { transform: translate(80px, 60px) scale(1.15); }
          66% { transform: translate(-50px, 100px) scale(0.9); }
        }
        @keyframes aurora-float-1 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          33% { transform: translate(-100px, 80px) scale(1.1); }
          66% { transform: translate(60px, -50px) scale(0.95); }
        }
        @keyframes aurora-float-2 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          33% { transform: translate(70px, -80px) scale(1.2); }
          66% { transform: translate(-60px, 40px) scale(0.85); }
        }
      `}</style>
    </div>
  );
}