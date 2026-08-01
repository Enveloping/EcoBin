import { myDeliveries } from '../../api/delivery'
import {
  toDeliveryListItem,
  type DeliveryListItem,
} from '../../utils/user-view'

Page({
  data: {
    list: [] as DeliveryListItem[],
    nextCursor: null as string | null,
    loading: false,
    finished: false,
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
  },

  onLoad() {
    void this.reload()
  },

  onPullDownRefresh() {
    void this.reload(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    void this.loadMore()
  },

  async reload(done?: () => void) {
    this.setData({
      list: [],
      nextCursor: null,
      finished: false,
    })
    try {
      await this.fetch()
    } finally {
      done?.()
    }
  },

  async loadMore() {
    if (this.data.loading || this.data.finished) return
    await this.fetch(this.data.nextCursor ?? undefined)
  },

  async fetch(cursor?: string) {
    if (this.data.loading) return
    this.setData({ loading: true })
    try {
      const result = await myDeliveries({ cursor, limit: 20 })
      const rows = result.items.map(toDeliveryListItem)
      this.setData({
        list: cursor ? this.data.list.concat(rows) : rows,
        nextCursor: result.nextCursor,
        finished: !result.nextCursor,
      })
    } finally {
      this.setData({ loading: false })
    }
  },
})
