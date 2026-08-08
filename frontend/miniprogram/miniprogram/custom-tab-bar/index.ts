import { startDoorEntry } from '../utils/door-entry'
import { startCleaningEntry } from '../utils/cleaning-entry'
import { getEntryMode } from '../utils/auth'
import { getDisplayedEntryMode } from '../utils/test-entry-preview'
import type { EntryMode } from '../types/api'

interface VisualTab {
  text: string
  icon: string
  pagePath?: string
  action?: 'scan'
}

interface TabBarData {
  selected: number
  hidden: boolean
  tabs: VisualTab[]
}

interface TabBarMethods {
  [key: string]: (...args: any[]) => any
  init(): void
  refresh(): void
  setHidden(hidden: boolean): void
  onTap(e: WechatMiniprogram.TouchEvent): void
}

type TabBarInstance = WechatMiniprogram.Component.Instance<TabBarData, {}, TabBarMethods>

function tabsFor(mode: EntryMode | undefined): VisualTab[] {
  switch (mode) {
    case 'CLEANING':
      return [
        { text: '首页', icon: 'home', pagePath: '/pages/clean/clean' },
        { text: '扫一扫', icon: 'scan', action: 'scan' },
        { text: '我的', icon: 'user', pagePath: '/pages/clean-profile/clean-profile' },
      ]
    case 'USER':
    default:
      return [
        { text: '首页', icon: 'home', pagePath: '/pages/home/home' },
        { text: '扫一扫', icon: 'scan', action: 'scan' },
        { text: '我的', icon: 'user', pagePath: '/pages/profile/profile' },
      ]
  }
}

Component<TabBarData, {}, TabBarMethods>({
  data: {
    selected: 0,
    hidden: false,
    tabs: [
      { text: '首页', icon: 'home', pagePath: '/pages/home/home' },
      { text: '扫一扫', icon: 'scan', action: 'scan' },
      { text: '我的', icon: 'user', pagePath: '/pages/profile/profile' },
    ] as VisualTab[],
  },

  methods: {
    init(this: TabBarInstance) {
      this.setData({ hidden: false })
      this.refresh()
    },

    setHidden(this: TabBarInstance, hidden: boolean) {
      this.setData({ hidden })
    },

    refresh(this: TabBarInstance) {
      const pages = getCurrentPages()
      const current = pages[pages.length - 1]
      const route = current ? `/${current.route}` : ''
      const displayedMode = getDisplayedEntryMode(getEntryMode())
      const isProfile =
        route === '/pages/profile/profile'
        || route === '/pages/clean-profile/clean-profile'
      this.setData({
        selected: isProfile ? 2 : 0,
        tabs: tabsFor(displayedMode),
      })
    },

    onTap(this: TabBarInstance, e: WechatMiniprogram.TouchEvent) {
      const index = Number(e.currentTarget.dataset.index)
      const item = this.data.tabs[index]
      if (!item) return
      if (item.action === 'scan') {
        const actualMode = getEntryMode()
        const displayedMode = getDisplayedEntryMode(actualMode)
        if (actualMode !== displayedMode) {
          wx.showToast({
            title: '当前仅切换界面，账号权限未改变',
            icon: 'none',
          })
          return
        }
        if (displayedMode === 'CLEANING') {
          startCleaningEntry()
        } else if (displayedMode === 'USER' || !displayedMode) {
          // 游客也必须能扫描设备公开码，这是首次创建机构账号的唯一入口。
          startDoorEntry()
        }
        return
      }
      if (item.pagePath) wx.switchTab({ url: item.pagePath })
    },
  },
})
