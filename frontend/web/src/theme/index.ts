/**
 * EcoBin 主题配置 - 「翡翠绿」配色方案
 * 主色: Emerald (#10B981) - 亮眼的绿色，呼应环保主题
 */

import type { ThemeConfig } from 'antd';

/**
 * 调色板 - 全局唯一颜色真源。组件禁止硬编码 hex，统一从这里取。
 */
export const palette = {
  primary: '#10B981', // Emerald-500 翡翠绿（亮眼）
  primaryHover: '#059669', // Emerald-600
  primaryActive: '#047857', // Emerald-700
  primaryBg: '#ECFDF5', // Emerald-50 浅底
  primarySoft: '#6EE7B7', // Emerald-300 辅助
  primaryLight: '#34D399', // Emerald-400
  primaryRGB: '16, 185, 129', // rgba 场景用

  success: '#16A34A',
  warning: '#D97706',
  error: '#DC2626',
  info: '#0284C7',

  textPrimary: '#0F172A',
  textRegular: '#334155',
  textSecondary: '#94A3B8',
  border: '#E2E8F0',
  bgLayout: '#F8FAFC',
  bgContainer: '#FFFFFF',

  sidebarBg: '#0F172A',
  sidebarText: '#E2E8F0',
} as const;

/** 生成带透明度的 rgba 颜色 */
export const alpha = (rgb: string, a: number) => `rgba(${rgb}, ${a})`;

/** 生成线性渐变 */
export const gradient = (from: string, to: string, angle = 135) =>
  `linear-gradient(${angle}deg, ${from} 0%, ${to} 100%)`;

/**
 * z-index 层级模型 - 对齐 Ant Design 层级
 */
export const z = {
  background: 0, // 背景动效（Aurora/Particle/Spotlight）
  content: 1, // 主内容
  dropdown: 1000, // 对齐 AntD
  modal: 1010,
  cursor: 99999, // 全局光标（最高）
} as const;

// 兼容旧引用（保留常量名，指向 palette）
const PRIMARY = palette.primary;
const PRIMARY_BG = palette.primaryBg;
const SUCCESS = palette.success;
const WARNING = palette.warning;
const ERROR = palette.error;
const INFO = palette.info;
const TEXT_PRIMARY = palette.textPrimary;
const TEXT_REGULAR = palette.textRegular;
const TEXT_SECONDARY = palette.textSecondary;
const BORDER = palette.border;
const BG_LAYOUT = palette.bgLayout;
const BG_CONTAINER = palette.bgContainer;
const SIDEBAR_BG = palette.sidebarBg;
const SIDEBAR_TEXT = palette.sidebarText;
const SIDEBAR_ACTIVE_BG = palette.primary;

/** 默认主题（亮色模式） */
export const lightTheme: ThemeConfig = {
  token: {
    colorPrimary: PRIMARY,
    colorSuccess: SUCCESS,
    colorWarning: WARNING,
    colorError: ERROR,
    colorInfo: INFO,

    // 文字色
    colorText: TEXT_REGULAR,
    colorTextHeading: TEXT_PRIMARY,
    colorTextSecondary: TEXT_SECONDARY,

    // 背景
    colorBgContainer: BG_CONTAINER,
    colorBgLayout: BG_LAYOUT,

    // 边框
    colorBorder: BORDER,
    colorBorderSecondary: BORDER,

    // 圆角
    borderRadius: 6,
    borderRadiusLG: 8,

    // 字体
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  },
  components: {
    Button: {
      borderRadius: 6,
    },
    Card: {
      borderRadiusLG: 12,
      boxShadowTertiary: '0 1px 2px rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02)',
    },
    Input: {
      borderRadius: 6,
    },
    Select: {
      borderRadius: 6,
    },
    Table: {
      headerBg: '#F1F5F9',
      headerColor: TEXT_PRIMARY,
      headerSplitColor: 'transparent',
      borderColor: BORDER,
      rowHoverBg: '#F0FDFA',
      cellPaddingBlock: 14,
      cellPaddingInline: 16,
      headerSortHoverBg: '#E2E8F0',
    },
    Tag: {
      borderRadiusSM: 4,
    },
    Descriptions: {
      itemColor: TEXT_SECONDARY,
      labelBg: '#F8FAFC',
      contentColor: TEXT_PRIMARY,
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: PRIMARY_BG,
      itemSelectedColor: PRIMARY,
      itemHoverBg: PRIMARY_BG,
      itemActiveBg: PRIMARY_BG,
    },
    Segmented: {
      trackBg: '#F1F5F9',
      itemSelectedBg: PRIMARY,
      itemSelectedColor: '#FFFFFF',
    },
  },
};

/** ProLayout 侧边栏深色主题 */
export const proLayoutDarkSidebar = {
  sidebarBackgroundColor: SIDEBAR_BG,
  sidebarTextColor: SIDEBAR_TEXT,
  sidebarActiveBackgroundColor: SIDEBAR_ACTIVE_BG,
  sidebarActiveTextColor: '#FFFFFF',
  headerBackgroundColor: BG_CONTAINER,
};

/** 登录页渐变背景色（由 palette 派生） */
export const loginGradient = {
  from: palette.primaryHover,
  via: palette.primary,
  to: palette.primaryLight,
};

/** 统计卡片渐变背景（由 palette 派生） */
export const statCardGradients = {
  primary: gradient(palette.primary, palette.primaryLight),
  success: gradient(palette.success, '#22C55E'),
  warning: gradient(palette.warning, '#F59E0B'),
  info: gradient(palette.info, '#38BDF8'),
};