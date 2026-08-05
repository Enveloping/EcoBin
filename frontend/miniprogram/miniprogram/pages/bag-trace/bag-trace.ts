import { bagUseCycles } from '../../api/bag-trace'
import { requireEntryMode } from '../../utils/guard'
import { formatLocalDateTime } from '../../utils/local-time'
import type { BagUseCycleItem } from '../../types/api'

interface CycleView extends BagUseCycleItem {
  statusText: string
  periodText: string
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
  requestGeneration: 0,

  data: {
    bagQrText: '',
    authText: '',
    legacyHint: '',
    cycles: [] as CycleView[],
    nextCursor: null as string | null,
    loading: false,
    errorMessage: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    if (!requireEntryMode(['CLEANING'])) return
    const bagQr = decode(options.bagQr)
    if (bagQr) {
      void this.loadBag(bagQr)
      return
    }
    if (options.scan === '1') void this.scanBag()
  },

  onPullDownRefresh() {
    if (!this.bagQr) {
      wx.stopPullDownRefresh()
      return
    }
    void this.loadBag(this.bagQr, () => wx.stopPullDownRefresh())
  },

  onUnload() {
    this.requestGeneration += 1
  },

  async scanBag() {
    try {
      const result = await wx.scanCode({ scanType: ['qrCode', 'barCode'] })
      const bagQr = String(result.result || '').trim()
      if (!bagQr) {
        wx.showToast({ title: '未识别到袋码', icon: 'none' })
        return
      }
      await this.loadBag(bagQr)
    } catch {
      // 用户主动取消扫码时保持当前页面，不显示误导性错误。
    }
  },

  async loadBag(bagQr: string, done?: () => void) {
    this.bagQr = bagQr
    const generation = ++this.requestGeneration
    this.setData({
      bagQrText: bagQr,
      cycles: [],
      nextCursor: null,
      loading: true,
      errorMessage: '',
    })
    try {
      const page = await bagUseCycles(bagQr)
      if (generation !== this.requestGeneration) return
      this.setData({
        authText: page.codeAuthKind === 'HMAC_V1'
          ? '已验签袋码'
          : '历史袋码',
        legacyHint: page.unassignedLegacyDeliveryCount > 0
          ? `另有 ${page.unassignedLegacyDeliveryCount} 笔历史投递无法精确归入周期`
          : '',
        cycles: page.items.map((item) => this.cycleView(item)),
        nextCursor: page.nextCursor,
      })
    } catch {
      if (generation === this.requestGeneration) {
        this.setData({ errorMessage: '袋码不存在、无权查看或暂时无法查询' })
      }
    } finally {
      if (generation === this.requestGeneration) {
        this.setData({ loading: false })
      }
      done?.()
    }
  },

  async loadMore() {
    if (!this.bagQr || !this.data.nextCursor || this.data.loading) return
    const generation = this.requestGeneration
    this.setData({ loading: true })
    try {
      const page = await bagUseCycles(
        this.bagQr,
        this.data.nextCursor,
      )
      if (generation !== this.requestGeneration) return
      this.setData({
        cycles: this.data.cycles.concat(
          page.items.map((item) => this.cycleView(item)),
        ),
        nextCursor: page.nextCursor,
      })
    } finally {
      if (generation === this.requestGeneration) {
        this.setData({ loading: false })
      }
    }
  },

  cycleView(item: BagUseCycleItem): CycleView {
    const start = formatLocalDateTime(item.installedAt)
    const end = item.removedAt
      ? formatLocalDateTime(item.removedAt)
      : '使用中'
    return {
      ...item,
      statusText: item.status === 'ACTIVE' ? '当前使用中' : '已清运',
      periodText: `${start} 至 ${end}`,
    }
  },

  onCycleTap(event: WechatMiniprogram.TouchEvent) {
    const cycleUid = String(event.currentTarget.dataset.cycleUid || '')
    if (!cycleUid || !this.bagQr) return
    wx.navigateTo({
      url: '/pages/bag-trace-orders/bag-trace-orders'
        + `?bagQr=${encodeURIComponent(this.bagQr)}`
        + `&cycleUid=${encodeURIComponent(cycleUid)}`,
    })
  },
})
