import { FEATURES } from '../config/index'
import type { EntryMode } from '../types/api'
import { entryUrlFor, getEntryMode } from './auth'

const PREVIEW_MODES: ReadonlyArray<{
  label: string
  mode?: EntryMode
}> = [
  { label: '跟随登录身份' },
  { label: '用户端', mode: 'USER' },
  { label: '清运端', mode: 'CLEANING' },
]

export const ENTRY_PREVIEW_NOTICE = '仅切换界面，当前账号权限未改变'

/** 发布版强制关闭，避免测试入口随配置误带到生产环境。 */
export function isEntryPreviewEnabled(): boolean {
  if (!FEATURES.entryPreview) return false
  try {
    return wx.getAccountInfoSync().miniProgram.envVersion !== 'release'
  } catch (error) {
    return false
  }
}

export function getDisplayedEntryMode(
  realMode = getEntryMode(),
): EntryMode | undefined {
  if (!realMode || !isEntryPreviewEnabled()) return realMode
  return getApp<IAppOption>().globalData.testViewMode ?? realMode
}

export function isCrossIdentityPreview(): boolean {
  const realMode = getEntryMode()
  const displayedMode = getDisplayedEntryMode(realMode)
  return !!realMode && !!displayedMode && realMode !== displayedMode
}

export function clearEntryPreview(): void {
  const app = getApp<IAppOption>()
  app.globalData.testViewMode = undefined
}

export function setEntryPreview(mode?: EntryMode): void {
  const app = getApp<IAppOption>()
  app.globalData.testViewMode = mode
}

function routeToPreview(mode?: EntryMode): void {
  const destination = mode ?? getEntryMode()
  if (!destination) {
    wx.reLaunch({ url: '/pages/home/home' })
    return
  }
  wx.reLaunch({ url: entryUrlFor(destination) })
}

export function showEntryPreviewSwitcher(): void {
  if (!isEntryPreviewEnabled()) return
  wx.showActionSheet({
    itemList: PREVIEW_MODES.map((item) => item.label),
    success: ({ tapIndex }) => {
      const selected = PREVIEW_MODES[tapIndex]
      if (!selected) return
      setEntryPreview(selected.mode)
      routeToPreview(selected.mode)
    },
  })
}
