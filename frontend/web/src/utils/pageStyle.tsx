/**
 * 页面级共享样式常量与工具
 */

import type { ReactNode } from 'react';

/** PageContainer 统一头部配置（标题 + 副标题） */
export function pageHeader(title: ReactNode, subTitle?: ReactNode) {
  return {
    title: <span style={{ fontWeight: 600 }}>{title}</span>,
    subTitle,
    header: { style: { paddingBottom: 16 } },
    style: { paddingBottom: 0 },
  };
}

/** ProTable 统一配置：卡片化、去默认边框 */
export const proTableConfig = {
  cardBordered: false,
  options: {
    density: false,
    fullScreen: false,
    reload: true,
    setting: false,
  },
  search: { labelWidth: 'auto' as const },
};

/** 统一的"危险操作"链接颜色 */
export const DANGER_COLOR = '#DC2626';
