import type { ThemeConfig } from 'antd';

/** Calm enterprise palette: one green accent, cool neutral surfaces. */
export const palette = {
  primary: '#0F766E',
  primaryHover: '#0B655E',
  primaryActive: '#09534E',
  primaryBg: '#EAF6F2',
  primaryRGB: '15, 118, 110',

  success: '#15803D',
  warning: '#B45309',
  error: '#B91C1C',
  info: '#0369A1',

  textPrimary: '#0F172A',
  textRegular: '#334155',
  textSecondary: '#64748B',
  border: '#E2E8F0',
  borderStrong: '#CBD5E1',
  bgLayout: '#F8FAFC',
  bgSubtle: '#F1F5F9',
  bgContainer: '#FFFFFF',
  onPrimary: '#FFFFFF',
} as const;

export const alpha = (rgb: string, opacity: number) =>
  `rgba(${rgb}, ${opacity})`;

export const lightTheme: ThemeConfig = {
  token: {
    colorPrimary: palette.primary,
    colorPrimaryHover: palette.primaryHover,
    colorPrimaryActive: palette.primaryActive,
    colorSuccess: palette.success,
    colorWarning: palette.warning,
    colorError: palette.error,
    colorInfo: palette.info,
    colorText: palette.textRegular,
    colorTextHeading: palette.textPrimary,
    colorTextSecondary: palette.textSecondary,
    colorBgContainer: palette.bgContainer,
    colorBgLayout: palette.bgLayout,
    colorFillAlter: palette.bgSubtle,
    colorBorder: palette.borderStrong,
    colorBorderSecondary: palette.border,
    borderRadius: 6,
    borderRadiusLG: 10,
    controlHeight: 36,
    fontSize: 14,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", Arial, sans-serif',
  },
  components: {
    Button: {
      borderRadius: 6,
      primaryShadow: 'none',
      defaultShadow: 'none',
      dangerShadow: 'none',
    },
    Card: {
      borderRadiusLG: 10,
      boxShadowTertiary: '0 1px 2px rgba(15, 23, 42, 0.04)',
    },
    Input: {
      borderRadius: 6,
    },
    Select: {
      borderRadius: 6,
    },
    Table: {
      headerBg: palette.bgSubtle,
      headerColor: palette.textPrimary,
      headerSplitColor: 'transparent',
      borderColor: palette.border,
      rowHoverBg: '#F3F8F7',
      cellPaddingBlock: 13,
      cellPaddingInline: 16,
      headerSortHoverBg: '#E8EEF2',
    },
    Tag: {
      borderRadiusSM: 4,
    },
    Descriptions: {
      labelBg: palette.bgSubtle,
      contentColor: palette.textPrimary,
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: palette.primaryBg,
      itemSelectedColor: palette.primary,
      itemHoverBg: '#F3F8F7',
      itemActiveBg: palette.primaryBg,
    },
    Segmented: {
      trackBg: palette.bgSubtle,
      itemSelectedBg: palette.bgContainer,
      itemSelectedColor: palette.primary,
    },
    Modal: {
      titleColor: palette.textPrimary,
    },
  },
};
