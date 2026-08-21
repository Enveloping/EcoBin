import { deliveryDetail } from '../../api/delivery'
import {
  formatMoneyCny,
  formatUnitPrice,
} from '../../utils/decimal'
import { formatLocalDateTime } from '../../utils/local-time'
import { MiniappApiProblem } from '../../utils/request'
import type { MiniappDeliveryAnomaly } from '../../types/api'

interface AnomalyView extends MiniappDeliveryAnomaly {
  timeText: string
}

function decodeOrderNo(value: string | undefined): string {
  if (!value) return ''
  try {
    return decodeURIComponent(value).trim()
  } catch {
    return ''
  }
}

function formatWeight(value: string | null): string {
  if (!value) return '重量待认定'
  const match = /^(-?)(0|[1-9]\d*)(?:\.(\d+))?$/.exec(value)
  if (!match) return '重量待认定'
  const fraction = (match[3] ?? '').replace(/0+$/, '')
  return `${match[1]}${match[2]}${fraction ? `.${fraction}` : ''} kg`
}

function formatAmount(
  value: string | null,
  approved: boolean,
): string {
  if (!value) return '金额待认定'
  const formatted = formatMoneyCny(value)
  if (formatted === '—') return '金额待认定'
  const negative = formatted.startsWith('-')
  const absolute = negative ? formatted.slice(1) : formatted
  if (approved) return negative ? `-¥${absolute}` : `+¥${absolute}`
  return negative ? `设备上报 -¥${absolute}` : `预计 ¥${absolute}`
}

Page({
  deliveryOrderNo: '',
  requestGeneration: 0,

  data: {
    loading: true,
    errorMessage: '',
    orderNo: '',
    statusText: '',
    statusTone: 'pending',
    portText: '',
    occurredAtText: '',
    deliveryAtText: '',
    weightText: '',
    weightCaption: '',
    amountText: '',
    amountCaption: '',
    unitPriceText: '',
    approvedAtText: '',
    reason: '',
    anomalies: [] as AnomalyView[],
  },

  onLoad(options: Record<string, string | undefined>) {
    this.deliveryOrderNo = decodeOrderNo(options.deliveryOrderNo)
    if (!this.deliveryOrderNo) {
      this.setData({
        loading: false,
        errorMessage: '缺少投递订单编号',
      })
      return
    }
    this.setData({ orderNo: this.deliveryOrderNo })
    void this.loadDetail()
  },

  onPullDownRefresh() {
    void this.loadDetail(() => wx.stopPullDownRefresh())
  },

  onUnload() {
    this.requestGeneration += 1
  },

  async loadDetail(done?: () => void) {
    if (!this.deliveryOrderNo) {
      done?.()
      return
    }
    const generation = ++this.requestGeneration
    this.setData({ loading: true, errorMessage: '' })
    try {
      const detail = await deliveryDetail(this.deliveryOrderNo, false)
      if (generation !== this.requestGeneration) return
      const approved = detail.review.status === 'APPROVED'
      const weightReliable = detail.raw.weightReliability === 'RELIABLE'
      const amountReliable = detail.raw.amountReliability === 'RELIABLE'
      const weight = approved
        ? detail.review.finalWeightKg
        : weightReliable
          ? detail.raw.weightKg
          : null
      const amount = approved
        ? detail.review.finalAmountYuan
        : amountReliable
          ? detail.raw.amountYuan
          : null
      this.setData({
        orderNo: detail.deliveryOrderNo,
        statusText: approved ? '已审核' : '待审核',
        statusTone: approved ? 'approved' : 'pending',
        portText: `${detail.source.portNo} 号投口`,
        occurredAtText: formatLocalDateTime(
          detail.source.deviceOccurredAt ?? detail.source.receivedAt,
        ),
        deliveryAtText: formatLocalDateTime(
          detail.source.deviceOccurredAt ?? detail.source.receivedAt,
        ),
        weightText: formatWeight(weight),
        weightCaption: approved
          ? '审核确认重量'
          : weightReliable
            ? '设备上报，等待审核'
            : '设备重量不可靠，等待人工认定',
        amountText: formatAmount(amount, approved),
        amountCaption: approved
          ? '审核确认返现'
          : amountReliable
            ? '预计返现，审核后才会入账'
            : '返现金额等待人工认定',
        unitPriceText: detail.raw.unitPriceYuanPerKg
          ? formatUnitPrice(detail.raw.unitPriceYuanPerKg)
          : '—',
        approvedAtText: formatLocalDateTime(detail.review.firstApprovedAt),
        reason: detail.review.reason ?? '',
        anomalies: detail.anomalies.map((item) => ({
          ...item,
          timeText: formatLocalDateTime(item.detectedAt),
        })),
      })
    } catch (error) {
      if (generation !== this.requestGeneration) return
      const notFound =
        error instanceof MiniappApiProblem && error.status === 404
      this.setData({
        errorMessage: notFound
          ? '订单不存在或无权查看'
          : '投递订单详情暂时无法加载',
      })
    } finally {
      if (generation === this.requestGeneration) {
        this.setData({ loading: false })
      }
      done?.()
    }
  },

  onRetry() {
    void this.loadDetail()
  },
})
