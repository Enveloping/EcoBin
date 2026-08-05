import { bagCycleOrders } from '../../api/bag-trace'
import { formatLocalDateTime } from '../../utils/local-time'
import type { BagTraceDeliveryOrderItem } from '../../types/api'

interface OrderView extends BagTraceDeliveryOrderItem {
  timeText: string
  weightText: string
  reviewText: string
}

function decode(value: string | undefined): string {
  if (!value) return ''
  try {
    return decodeURIComponent(value).trim()
  } catch {
    return ''
  }
}

Page({
  bagQr: '',
  cycleUid: '',
  requestGeneration: 0,

  data: {
    orders: [] as OrderView[],
    nextCursor: null as string | null,
    loading: false,
    errorMessage: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    this.bagQr = decode(options.bagQr)
    this.cycleUid = decode(options.cycleUid)
    if (!this.bagQr || !this.cycleUid) {
      this.setData({ errorMessage: '缺少袋码使用周期参数' })
      return
    }
    void this.reload()
  },

  onPullDownRefresh() {
    void this.reload(() => wx.stopPullDownRefresh())
  },

  onUnload() {
    this.requestGeneration += 1
  },

  async reload(done?: () => void) {
    const generation = ++this.requestGeneration
    this.setData({
      orders: [],
      nextCursor: null,
      loading: true,
      errorMessage: '',
    })
    try {
      const page = await bagCycleOrders(this.bagQr, this.cycleUid)
      if (generation !== this.requestGeneration) return
      this.setData({
        orders: page.items.map((item) => this.view(item)),
        nextCursor: page.nextCursor,
      })
    } catch {
      if (generation === this.requestGeneration) {
        this.setData({ errorMessage: '该周期不存在或投递记录暂时无法加载' })
      }
    } finally {
      if (generation === this.requestGeneration) {
        this.setData({ loading: false })
      }
      done?.()
    }
  },

  async loadMore() {
    const cursor = this.data.nextCursor
    if (!cursor || this.data.loading) return
    const generation = this.requestGeneration
    this.setData({ loading: true })
    try {
      const page = await bagCycleOrders(
        this.bagQr,
        this.cycleUid,
        cursor,
      )
      if (generation !== this.requestGeneration) return
      this.setData({
        orders: this.data.orders.concat(
          page.items.map((item) => this.view(item)),
        ),
        nextCursor: page.nextCursor,
      })
    } catch {
      wx.showToast({ title: '更多投递加载失败', icon: 'none' })
    } finally {
      if (generation === this.requestGeneration) {
        this.setData({ loading: false })
      }
    }
  },

  view(item: BagTraceDeliveryOrderItem): OrderView {
    const weight = item.finalWeightKg ?? item.rawWeightKg
    return {
      ...item,
      timeText: formatLocalDateTime(
        item.deviceOccurredAt ?? item.receivedAt,
      ),
      weightText: weight ? `${weight} kg` : '重量待认定',
      reviewText: item.reviewStatus === 'APPROVED' ? '已审核' : '待审核',
    }
  },

  onOrderTap(event: WechatMiniprogram.TouchEvent) {
    const orderNo = String(event.currentTarget.dataset.orderNo || '')
    if (!orderNo) return
    wx.navigateTo({
      url: '/pages/bag-trace-detail/bag-trace-detail'
        + `?bagQr=${encodeURIComponent(this.bagQr)}`
        + `&cycleUid=${encodeURIComponent(this.cycleUid)}`
        + `&deliveryOrderNo=${encodeURIComponent(orderNo)}`,
    })
  },
})
