import { myDeliveries } from '../../api/delivery'
import { FEATURES } from '../../config/index'
import {
  filterDeliveries,
  toDeliveryListItem,
  type DeliveryFilter,
  type DeliveryListItem,
} from '../../utils/user-view'

interface FilterTab {
  key: DeliveryFilter
  text: string
}

Page({
  requestGeneration: 0,

  data: {
    tabs: [
      { key: 'ALL', text: '全部' },
      { key: 'PENDING', text: '待审核' },
      { key: 'APPROVED', text: '审核通过' },
    ] as FilterTab[],
    active: 'ALL' as DeliveryFilter,
    serviceUnavailable: !FEATURES.targetUserDataApi,
    allOrders: [] as DeliveryListItem[],
    visibleOrders: [] as DeliveryListItem[],
    page: 0,
    pageSize: 20,
    total: 0,
    loading: false,
    initialLoading: FEATURES.targetUserDataApi,
    finished: !FEATURES.targetUserDataApi,
    errorMessage: '',
  },

  onLoad() {
    if (!FEATURES.targetUserDataApi) return
    void this.reload()
  },

  onPullDownRefresh() {
    if (!FEATURES.targetUserDataApi) {
      wx.stopPullDownRefresh()
      return
    }
    void this.reload(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (!FEATURES.targetUserDataApi) return
    void this.loadMore()
  },

  onTab(e: WechatMiniprogram.TouchEvent) {
    const active = String(e.currentTarget.dataset.tab) as DeliveryFilter
    if (!this.data.tabs.some((tab) => tab.key === active)) return
    const visibleOrders = filterDeliveries(this.data.allOrders, active)
    this.setData({ active, visibleOrders })
    if (
      visibleOrders.length === 0
      && !this.data.finished
      && !this.data.loading
    ) {
      void this.loadMore()
    }
  },

  async reload(done?: () => void) {
    if (!FEATURES.targetUserDataApi) {
      done?.()
      return
    }
    const generation = ++this.requestGeneration
    this.setData({
      allOrders: [],
      visibleOrders: [],
      page: 0,
      total: 0,
      finished: false,
      loading: false,
      initialLoading: true,
      errorMessage: '',
    })
    try {
      await this.fetchPage(1, generation)
    } finally {
      done?.()
    }
  },

  async loadMore() {
    if (!FEATURES.targetUserDataApi) return
    if (this.data.loading || this.data.finished) return
    await this.fetchPage(this.data.page + 1)
  },

  async fetchPage(page: number, generation?: number) {
    const activeGeneration = generation ?? this.requestGeneration
    if (activeGeneration !== this.requestGeneration) return
    if (this.data.loading) return
    this.setData({ loading: true, errorMessage: '' })
    try {
      const result = await myDeliveries(page, this.data.pageSize, false)
      if (activeGeneration !== this.requestGeneration) return
      const rows = result.records.map(toDeliveryListItem)
      const allOrders = page === 1
        ? rows
        : this.data.allOrders.concat(rows)
      this.setData({
        allOrders,
        visibleOrders: filterDeliveries(allOrders, this.data.active),
        page,
        total: result.total,
        finished: allOrders.length >= result.total,
      })
    } catch (error) {
      if (activeGeneration !== this.requestGeneration) return
      if (this.data.allOrders.length === 0) {
        this.setData({ errorMessage: '投递订单暂时无法加载' })
      } else {
        wx.showToast({ title: '更多订单加载失败', icon: 'none' })
      }
    } finally {
      if (activeGeneration === this.requestGeneration) {
        this.setData({ loading: false, initialLoading: false })
      }
    }
  },

  onRetry() {
    void this.reload()
  },

  onContinueLoading() {
    void this.loadMore()
  },

  onOrderTap(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id)
    const order = this.data.allOrders.find((item) => item.id === id)
    if (!order) return
    wx.showModal({
      title: order.categoryText,
      content: [
        `订单：${order.orderSn}`,
        `重量：${order.weightText}`,
        `金额：${order.amountText}`,
        `状态：${order.statusText}`,
        `时间：${order.timeText}`,
      ].join('\n'),
      showCancel: false,
      confirmText: '知道了',
    })
  },
})
