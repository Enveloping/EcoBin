/**
 * 粒子连线背景组件
 * 纯 Canvas 实现，无第三方依赖。粒子随机漂浮，相近粒子之间连线，
 * 鼠标移动时附近粒子被轻微吸引。适合登录页等全屏背景。
 *
 * 性能优化：粒子上限 90、连线用平方距离比较避免 sqrt、pointerEvents:none 不拦截事件、
 * 鼠标坐标监听 window（背景层不应捕获事件）。
 */

import { useEffect, useRef } from 'react';
import { palette } from '@/theme';

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
}

interface Props {
  color?: string;
  density?: number;
  linkDistance?: number;
  mouseRadius?: number;
  maxParticles?: number;
}

export default function ParticleBackground({
  color = '255, 255, 255',
  density = 0.1,
  linkDistance = 130,
  mouseRadius = 150,
  maxParticles = 90,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const mouseRef = useRef({ x: -1000, y: -1000 });
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let width = 0;
    let height = 0;
    const dpr = window.devicePixelRatio;

    const initParticles = () => {
      width = canvas.width = canvas.offsetWidth * dpr;
      height = canvas.height = canvas.offsetHeight * dpr;
      const count = Math.min(
        maxParticles,
        Math.floor((width * height) / (10000 * dpr * dpr) * density),
      );
      particlesRef.current = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.5 * dpr,
        vy: (Math.random() - 0.5) * 0.5 * dpr,
        radius: (Math.random() * 1.8 + 0.8) * dpr,
      }));
    };

    const linkDistSq = (linkDistance * dpr) ** 2;
    const mouseRadSq = (mouseRadius * dpr) ** 2;

    const draw = () => {
      ctx.clearRect(0, 0, width, height);
      const particles = particlesRef.current;

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        p.x += p.vx;
        p.y += p.vy;

        if (p.x < 0 || p.x > width) p.vx = -p.vx;
        if (p.y < 0 || p.y > height) p.vy = -p.vy;

        // 鼠标吸引（用平方距离避免 sqrt）
        const dx = mouseRef.current.x - p.x;
        const dy = mouseRef.current.y - p.y;
        const distSq = dx * dx + dy * dy;
        if (distSq < mouseRadSq && distSq > 0) {
          const dist = Math.sqrt(distSq);
          const force = (mouseRadius * dpr - dist) / (mouseRadius * dpr);
          p.x += (dx / dist) * force * 0.8;
          p.y += (dy / dist) * force * 0.8;
        }

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${color}, 0.8)`;
        ctx.fill();

        // 连线：合并 path 后按透明度 stroke
        ctx.beginPath();
        for (let j = i + 1; j < particles.length; j++) {
          const q = particles[j];
          const ldx = p.x - q.x;
          const ldy = p.y - q.y;
          const ldistSq = ldx * ldx + ldy * ldy;
          if (ldistSq < linkDistSq) {
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(q.x, q.y);
          }
        }
        ctx.strokeStyle = `rgba(${color}, 0.35)`;
        ctx.lineWidth = dpr;
        ctx.stroke();
      }
      rafRef.current = requestAnimationFrame(draw);
    };

    // 监听 window 的 mousemove，换算到 canvas 坐标（背景层不拦截事件）
    const handleMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseRef.current = {
        x: (e.clientX - rect.left) * dpr,
        y: (e.clientY - rect.top) * dpr,
      };
    };
    const handleMouseLeave = () => {
      mouseRef.current = { x: -1000, y: -1000 };
    };

    initParticles();
    draw();
    window.addEventListener('resize', initParticles);
    window.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseleave', handleMouseLeave);

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener('resize', initParticles);
      window.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseleave', handleMouseLeave);
    };
  }, [color, density, linkDistance, mouseRadius, maxParticles]);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        zIndex: 0,
      }}
    />
  );
}