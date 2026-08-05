/**
 * 页面级共享样式常量与工具
 */

import type { ReactNode } from 'react';
import { palette } from '@/theme';

/** PageContainer 统一头部配置（标题 + 副标题） */
export function pageHeader(title: ReactNode, subTitle?: ReactNode) {
  return {
    title: <span className="page-title">{title}</span>,
    subTitle,
    header: { style: { paddingBottom: 13 } },
    style: { paddingBottom: 0 },
  };
}

/** ProTable 统一配置：卡片化、去默认边框 */
export const proTableConfig = {
  className: 'data-table-workbench',
  cardBordered: false,
  options: {
    density: false,
    fullScreen: false,
    reload: true,
    setting: true,
  },
  search: { labelWidth: 'auto' as const },
  scroll: { x: 'max-content' as const },
  pagination: {
    showSizeChanger: true,
    showTotal: (total: number) => `共 ${total} 条`,
  },
};

/** 统一的"危险操作"链接颜色 */
export const DANGER_COLOR = palette.error;
