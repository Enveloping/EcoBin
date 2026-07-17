/**
 * 角色 → 底部 tab 列表映射（数据驱动）。
 *
 * 自定义 tabBar 组件按 globalData.role 取对应列表渲染。
 * 扩展新角色或调整 tab 只改此文件，无需动组件逻辑。
 */

/** 角色常量，对应后端 UserRole */
export const ROLE = {
  USER: 1, // 普通用户
  CLEANER: 2, // 清运员
  DEVICE_ADMIN: 3, // 设备管理员（本期不单独做页面，登录后按清运员能力展示）
} as const

export interface TabItem {
  /** 页面路径（须在 app.json pages 中，且能被 switchTab 跳转） */
  pagePath: string
  /** tab 文案 */
  text: string
  /** TDesign 图标名（t-icon name） */
  icon: string
}

const TAB_HOME: TabItem = { pagePath: '/pages/home/home', text: '首页', icon: 'home' }
const TAB_CLEAN: TabItem = { pagePath: '/pages/clean/clean', text: '清运', icon: 'tools' }
const TAB_RECORDS: TabItem = { pagePath: '/pages/records/records', text: '投递记录', icon: 'root-list' }
const TAB_WALLET: TabItem = { pagePath: '/pages/wallet/wallet', text: '钱包', icon: 'wallet' }
const TAB_PROFILE: TabItem = { pagePath: '/pages/profile/profile', text: '我的', icon: 'user' }

/** 普通用户：投递 + 钱包 + 我的 */
const USER_TABS: TabItem[] = [TAB_HOME, TAB_RECORDS, TAB_WALLET, TAB_PROFILE]

/** 清运员/设备管理员：在普通用户基础上多「清运」 */
const CLEANER_TABS: TabItem[] = [TAB_HOME, TAB_CLEAN, TAB_RECORDS, TAB_WALLET, TAB_PROFILE]

const ROLE_TABS: Record<number, TabItem[]> = {
  [ROLE.USER]: USER_TABS,
  [ROLE.CLEANER]: CLEANER_TABS,
  [ROLE.DEVICE_ADMIN]: CLEANER_TABS,
}

/** 取某角色的 tab 列表，未知角色兜底为普通用户 */
export function getTabsByRole(role: number | undefined): TabItem[] {
  if (role && ROLE_TABS[role]) {
    return ROLE_TABS[role]
  }
  return USER_TABS
}

/** 角色中文名 */
export function roleName(role: number | undefined): string {
  switch (role) {
    case ROLE.USER:
      return '普通用户'
    case ROLE.CLEANER:
      return '清运员'
    case ROLE.DEVICE_ADMIN:
      return '设备管理员'
    default:
      return '未知'
  }
}
