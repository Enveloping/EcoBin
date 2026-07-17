import { myDeliveries } from '../../api/delivery'
import type { DeliveryOrder } from '../../types/api'

const AUDIT_STATUS: Record<number, string> = {
  0: '待审核',
  1: '已通过',
  2: '已驳回',
}

interface Row extends DeliveryOrder {
  statusText: string
  auditStatusText: string
  weightText: string
  amountText: string
}

Page({
  data: {
    list: [] as Row[],
    page: 1,
    pageSize: 20,
    total: 0,
    loading: false,
    finished: false,
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
  },

  onLoad() {
    this.reload()
  },

  onPullDownRefresh() {
    this.reload(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    this.loadMore()
  },

  reload(done?: () => void) {
    this.setData({ page: 1, list: [], finished: false }, () => {
      this.fetch().then(done).catch(done)
    })
  },

  loadMore() {
    if (this.data.loading || this.data.finished) return
    this.setData({ page: this.data.page + 1 }, () => this.fetch())
  },

  async fetch() {
    if (this.data.loading) return
    this.setData({ loading: true })
    try {
      const res = await myDeliveries(this.data.page, this.data.pageSize)
      const rows = res.records.map((o) => this.toRow(o))
      const list = this.data.page === 1 ? rows : this.data.list.concat(rows)
      this.setData({
        list,
        total: res.total,
        finished: list.length >= res.total,
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  toRow(o: DeliveryOrder): Row {
    const audit = o.auditStatus ?? 0
    // 金额仅在审核通过后显示「已到账」，待审核/驳回时弱化提示，避免误以为已入账
    let amountText = ''
    if (o.price != null && o.weight != null) {
      const money = (o.price * o.weight).toFixed(2)
      if (audit === 1) amountText = `+¥${money}`
      else if (audit === 2) amountText = '未返现'
      else amountText = `待审核 ¥${money}`
    }
    return {
      ...o,
      statusText: o.deliveryStatus === 1 ? '已完成' : '进行中',
      auditStatusText: AUDIT_STATUS[audit] || '待审核',
      weightText: o.weight != null ? `${o.weight} kg` : '—',
      amountText,
    }
  },
})
