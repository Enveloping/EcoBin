import { bagCycleOrderDetail } from '../../api/bag-trace'
import { formatLocalDateTime } from '../../utils/local-time'
import type { MiniappDeliveryPhoto } from '../../types/api'

interface PhotoView extends MiniappDeliveryPhoto {
  positionText: string
  statusText: string
}

const POSITION_TEXT: Record<string, string> = {
  BEFORE_INNER: '投递前 · 内部',
  BEFORE_OUTER: '投递前 · 外部',
  AFTER_INNER: '投递后 · 内部',
  AFTER_OUTER: '投递后 · 外部',
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
  data: {
    loading: true,
    errorMessage: '',
    orderNo: '',
    nickname: '',
    avatarText: '用',
    phone: '',
    timeText: '',
    rawWeightText: '—',
    finalWeightText: '—',
    finalAmountText: '—',
    reviewText: '',
    reason: '',
    photos: [] as PhotoView[],
  },

  async onLoad(options: Record<string, string | undefined>) {
    const bagQr = decode(options.bagQr)
    const cycleUid = decode(options.cycleUid)
    const orderNo = decode(options.deliveryOrderNo)
    if (!bagQr || !cycleUid || !orderNo) {
      this.setData({ loading: false, errorMessage: '缺少投递溯源参数' })
      return
    }
    try {
      const detail = await bagCycleOrderDetail(bagQr, cycleUid, orderNo)
      const order = detail.order
      this.setData({
        orderNo: order.deliveryOrderNo,
        nickname: order.user.nickname,
        avatarText: order.user.nickname.slice(0, 1) || '用',
        phone: order.user.maskedPhoneNumber ?? '未绑定手机号',
        timeText: formatLocalDateTime(
          order.deviceOccurredAt ?? order.receivedAt,
        ),
        rawWeightText: order.rawWeightKg
          ? `${order.rawWeightKg} kg`
          : '—',
        finalWeightText: order.finalWeightKg
          ? `${order.finalWeightKg} kg`
          : '—',
        finalAmountText: order.finalAmountYuan
          ? `¥${order.finalAmountYuan}`
          : '—',
        reviewText: order.reviewStatus === 'APPROVED'
          ? '已审核'
          : '待审核',
        reason: order.reason ?? '',
        photos: detail.photos.map((photo) => ({
          ...photo,
          positionText: POSITION_TEXT[photo.position] ?? photo.position,
          statusText: photo.status === 'AVAILABLE'
            ? '已上传'
            : photo.status === 'UPLOAD_PENDING'
              ? '上传中'
              : '未取得',
        })),
      })
    } catch {
      this.setData({ errorMessage: '订单不存在、无权查看或暂时无法加载' })
    } finally {
      this.setData({ loading: false })
    }
  },

  onPreviewPhoto(event: WechatMiniprogram.TouchEvent) {
    const current = String(event.currentTarget.dataset.url || '')
    if (!current) return
    const urls = this.data.photos
      .filter((photo) => photo.status === 'AVAILABLE' && !!photo.url)
      .map((photo) => photo.url as string)
    if (urls.length) wx.previewImage({ current, urls })
  },
})
