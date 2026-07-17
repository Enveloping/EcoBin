import { startDoorEntry } from '../utils/door-entry'

interface VisualTab {
  text: string
  icon: string
  pagePath?: string
  action?: 'scan'
}

interface TabBarData {
  selected: number
  tabs: VisualTab[]
}

interface TabBarMethods {
  [key: string]: (...args: any[]) => any
  init(): void
  refresh(): void
  onTap(e: WechatMiniprogram.TouchEvent): void
}

type TabBarInstance = WechatMiniprogram.Component.Instance<TabBarData, {}, TabBarMethods>

Component<TabBarData, {}, TabBarMethods>({
  data: {
    selected: 0,
    tabs: [
      { text: '首页', icon: 'home', pagePath: '/pages/home/home' },
      { text: '扫一扫', icon: 'scan', action: 'scan' },
      { text: '我的', icon: 'user', pagePath: '/pages/profile/profile' },
    ] as VisualTab[],
  },

  methods: {
    init(this: TabBarInstance) {
      this.refresh()
    },

    refresh(this: TabBarInstance) {
      const pages = getCurrentPages()
      const current = pages[pages.length - 1]
      const route = current ? `/${current.route}` : ''
      this.setData({ selected: route === '/pages/profile/profile' ? 2 : 0 })
    },

    onTap(this: TabBarInstance, e: WechatMiniprogram.TouchEvent) {
      const index = Number(e.currentTarget.dataset.index)
      const item = this.data.tabs[index]
      if (!item) return
      if (item.action === 'scan') {
        startDoorEntry()
        return
      }
      if (item.pagePath) wx.switchTab({ url: item.pagePath })
    },
  },
})
