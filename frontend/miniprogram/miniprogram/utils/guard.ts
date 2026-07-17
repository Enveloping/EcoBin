/**
 * 页面级角色守卫：受限页面在 onLoad 调用，角色不符则弹回首页。
 * 与自定义 tabBar「不展示入口」形成双重保险。
 */
import { getRole } from './auth'

/**
 * 要求当前角色在 allowed 列表内，否则提示并跳回首页。
 * @returns 是否通过
 */
export function requireRole(allowed: number[]): boolean {
  const role = getRole()
  if (role && allowed.includes(role)) {
    return true
  }
  wx.showToast({ title: '无权访问该页面', icon: 'none' })
  setTimeout(() => {
    wx.switchTab({ url: '/pages/home/home' })
  }, 800)
  return false
}
