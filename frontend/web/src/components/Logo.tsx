/**
 * EcoBin Logo 组件
 */

import { palette } from '@/theme';

export default function EcoBinLogo({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: collapsed ? 'center' : 'flex-start',
        padding: collapsed ? '12px 0' : '12px 24px',
        gap: collapsed ? 0 : 12,
      }}
    >
      {/* Logo Icon - 回收符号简化版 */}
      <svg
        width={collapsed ? 32 : 36}
        height={collapsed ? 32 : 36}
        viewBox="0 0 48 48"
        fill="none"
        style={{ flexShrink: 0 }}
      >
        <circle cx="24" cy="24" r="22" fill={palette.primary} />
        <path
          d="M24 8C15.16 8 8 15.16 8 24C8 32.84 15.16 40 24 40C32.84 40 40 32.84 40 24"
          stroke="#FFFFFF"
          strokeWidth="3"
          strokeLinecap="round"
          fill="none"
        />
        <path
          d="M32 16L40 24L32 32"
          stroke="#FFFFFF"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="none"
        />
        <path
          d="M24 24L24 8"
          stroke="#FFFFFF"
          strokeWidth="3"
          strokeLinecap="round"
        />
      </svg>

      {!collapsed && (
        <div style={{ overflow: 'hidden' }}>
          <div
            style={{
              fontSize: 18,
              fontWeight: 600,
              color: palette.textPrimary,
              lineHeight: 1.2,
              whiteSpace: 'nowrap',
            }}
          >
            EcoBin
          </div>
          <div
            style={{
              fontSize: 12,
              color: palette.textSecondary,
              lineHeight: 1.4,
              whiteSpace: 'nowrap',
            }}
          >
            智慧环保
          </div>
        </div>
      )}
    </div>
  );
}