import { getEntryMode, hasCapability, routeToEntry } from './auth'
import type { EntryMode } from '../types/api'

function deny(): false {
  wx.showToast({ title: '无权访问该页面', icon: 'none' })
  setTimeout(() => routeToEntry(), 800)
  return false
}

/** 页面能力来自服务端会话，不再根据客户端数字角色推导。 */
export function requireEntryMode(allowed: EntryMode[]): boolean {
  const entryMode = getEntryMode()
  return entryMode && allowed.includes(entryMode) ? true : deny()
}

export function requireCapability(capability: string): boolean {
  return hasCapability(capability) ? true : deny()
}
