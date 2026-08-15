import { myDeliveries } from '../../api/delivery'
import { FEATURES } from '../../config/index'
import { MiniappApiProblem } from '../../utils/request'
import {
  toDeliveryListItem,
  type DeliveryFilter,
  type DeliveryListItem,
} from '../../utils/user-view'
import type { DeliveryReviewStatus } from '../../types/api'

interface FilterTab {
  key: DeliveryFilter
  text: string
}

const PAGE_SIZE = 20

function reviewStatus(filter: DeliveryFilter): DeliveryReviewStatus | undefined {
  return filter === 'ALL' ? undefined : filter
}

function initialFilter(reviewStatusValue: string | undefined): DeliveryFilter {
  return reviewStatusValue === 'PENDING' ? 'PENDING' : 'ALL'
}

Page({
  requestGeneration: 0,
  cursorRecoveryUsed: false,

  data: {
    tabs: [
      { key: 'ALL', text: '全部' },
      { key: 'PENDING', text: '待审核' },
      { key: 'APPROVED', text: '已审核' },
    ] as FilterTab[],
    active: 'ALL' as DeliveryFilter,
    currentFilterText: '全部订单',
    serviceUnavailable: !FEATURES.targetDeliveryOrderApi,
    orders: [] as DeliveryListItem[],
    nextCursor: null as string | null,
    loading: false,
    initialLoading: FEATURES.targetDeliveryOrderApi,
    finished: !FEATURES.targetDeliveryOrderApi,
    errorMessage: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    if (!FEATURES.targetDeliveryOrderApi) return
    const active = initialFilter(options.reviewStatus)
    this.setData({
      active,
      currentFilterText: active === 'PENDING' ? '待审核' : '全部订单',
    }, () => {
      void this.reload()
    })
  },

  onPullDownRefresh() {
    if (!FEATURES.targetDeliveryOrderApi) {
      wx.stopPullDownRefresh()
      return
    }
    void this.reload(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (!FEATURES.targetDeliveryOrderApi) return
    void this.loadMore()
  },

  onTab(event: WechatMiniprogram.TouchEvent) {
    const active = String(event.currentTarget.dataset.tab) as DeliveryFilter
    const tab = this.data.tabs.find((item) => item.key === active)
    if (!tab || active === this.data.active) return
    this.setData({
      active,
      currentFilterText: active === 'ALL' ? '全部订单' : tab.text,
    }, () => {
      void this.reload()
    })
  },

  async reload(
    done?: () => void,
    preserveCursorRecovery = false,
  ) {
    if (!FEATURES.targetDeliveryOrderApi) {
      done?.()
      return
    }
    if (!preserveCursorRecovery) this.cursorRecoveryUsed = false
    const generation = ++this.requestGeneration
    this.setData({
      orders: [],
      nextCursor: null,
      finished: false,
      loading: false,
      initialLoading: true,
      errorMessage: '',
    })
    try {
      await this.fetchNext(undefined, generation)
    } finally {
      done?.()
    }
  },

  async loadMore() {
    if (!FEATURES.targetDeliveryOrderApi) return
    if (this.data.loading || this.data.finished) return
    await this.fetchNext(this.data.nextCursor ?? undefined)
  },

  async fetchNext(cursor?: string, generation?: number) {
    const activeGeneration = generation ?? this.requestGeneration
    if (activeGeneration !== this.requestGeneration || this.data.loading) {
      return
    }
    this.setData({ loading: true, errorMessage: '' })
    let recoverInvalidCursor = false
    try {
      const result = await myDeliveries({
        cursor,
        limit: PAGE_SIZE,
        reviewStatus: reviewStatus(this.data.active),
      }, false)
      if (activeGeneration !== this.requestGeneration) return
      const rows = result.items.map(toDeliveryListItem)
      const orders = cursor ? this.data.orders.concat(rows) : rows
      this.setData({
        orders,
        nextCursor: result.nextCursor,
        finished: !result.nextCursor,
      })
    } catch (error) {
      if (activeGeneration !== this.requestGeneration) return
      if (
        cursor
        && !this.cursorRecoveryUsed
        && error instanceof MiniappApiProblem
        && error.code === 'COMMON.INVALID_CURSOR'
      ) {
        this.cursorRecoveryUsed = true
        recoverInvalidCursor = true
      } else if (this.data.orders.length === 0) {
        this.setData({ errorMessage: '投递订单暂时无法加载' })
      } else {
        wx.showToast({ title: '更多订单加载失败', icon: 'none' })
      }
    } finally {
      if (activeGeneration === this.requestGeneration) {
        this.setData({ loading: false, initialLoading: false })
      }
    }

    if (recoverInvalidCursor && activeGeneration === this.requestGeneration) {
      wx.showToast({ title: '订单列表已更新', icon: 'none' })
      await this.reload(undefined, true)
    }
  },

  onRetry() {
    void this.reload()
  },

  onContinueLoading() {
    void this.loadMore()
  },

  onOrderTap(event: WechatMiniprogram.TouchEvent) {
    const deliveryOrderNo = String(
      event.currentTarget.dataset.deliveryOrderNo || '',
    )
    if (!deliveryOrderNo) return
    wx.navigateTo({
      url: `/pages/order-detail/order-detail?deliveryOrderNo=${
        encodeURIComponent(deliveryOrderNo)
      }`,
    })
  },
})
