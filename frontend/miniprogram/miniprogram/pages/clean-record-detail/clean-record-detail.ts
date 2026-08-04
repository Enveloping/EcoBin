import { cleanRecordDetail } from '../../api/clean'
import type {
  CleanPhoto,
  DeliveryPhotoPosition,
  DeliveryPhotoStatus,
  MiniappCleanAnomaly,
} from '../../types/api'
import { getEntryMode } from '../../utils/auth'
import { requireEntryMode } from '../../utils/guard'
import { formatLocalDateTime } from '../../utils/local-time'
import { MiniappApiProblem } from '../../utils/request'

interface PhotoView extends CleanPhoto {
  positionText: string
  statusText: string
}

interface AnomalyView extends MiniappCleanAnomaly {
  timeText: string
}

const PHOTO_POSITION_TEXT: Record<DeliveryPhotoPosition, string> = {
  BEFORE_INNER: '清运前 · 内部',
  BEFORE_OUTER: '清运前 · 外部',
  AFTER_INNER: '清运后 · 内部',
  AFTER_OUTER: '清运后 · 外部',
}

const PHOTO_STATUS_TEXT: Record<DeliveryPhotoStatus, string> = {
  UPLOAD_PENDING: '上传中',
  AVAILABLE: '已上传',
  PERMANENTLY_MISSING: '未取得',
}

function decodeRecordNo(value?: string): string {
  if (!value) return ''
  try {
    const decoded = decodeURIComponent(value).trim()
    return /^[A-Za-z0-9_-]{8,64}$/.test(decoded) ? decoded : ''
  } catch {
    return ''
  }
}

function kg(value: string | null): string {
  return value ? `${value} kg` : '—'
}

function factStatus(status: string): string {
  const values: Record<string, string> = {
    RELIABLE: '可靠',
    FAILED: '失败',
    INVALID: '无效',
    UNAVAILABLE: '不可用',
    TRUSTED: '可信',
    UNTRUSTED: '不可信',
    MISSING: '缺失',
  }
  return values[status] ?? status
}

Page({
  cleanRecordNo: '',
  requestGeneration: 0,

  data: {
    previewOnly: false,
    loading: true,
    errorMessage: '',
    recordNo: '',
    resultText: '',
    resultTone: 'normal',
    completedAtText: '',
    deviceCompletedAtText: '',
    backendReceivedAtText: '',
    deploymentCode: '',
    portText: '',
    operationUid: '',
    eventUid: '',
    commandUid: '',
    configVersionText: '',
    removedBagQr: '',
    removedBagStateText: '',
    installedBagQr: '',
    effectiveWeightText: '',
    effectiveSourceText: '',
    includedInStatisticsText: '',
    recordRemark: '',
    preUnlockText: '',
    oldBaselineText: '',
    deviceRemovedText: '',
    recalculatedText: '',
    finalTotalText: '',
    candidateBaselineText: '',
    newBaselineText: '',
    detectionText: '',
    anomalies: [] as AnomalyView[],
    photos: [] as PhotoView[],
  },

  onLoad(options: Record<string, string | undefined>) {
    if (!requireEntryMode(['CLEANING'])) return
    const previewOnly = getEntryMode() !== 'CLEANING'
    this.cleanRecordNo = decodeRecordNo(options.cleanRecordNo)
    this.setData({ previewOnly, recordNo: this.cleanRecordNo })
    if (previewOnly) {
      this.setData({
        loading: false,
        errorMessage: '当前仅预览清运端，登录账号权限未改变，因此不会请求记录详情。',
      })
      return
    }
    if (!this.cleanRecordNo) {
      this.setData({ loading: false, errorMessage: '清运记录编号无效' })
      return
    }
    void this.loadDetail()
  },

  onPullDownRefresh() {
    void this.loadDetail(() => wx.stopPullDownRefresh())
  },

  onUnload() {
    this.requestGeneration += 1
  },

  async loadDetail(done?: () => void) {
    if (!this.cleanRecordNo || this.data.previewOnly) {
      done?.()
      return
    }
    const generation = ++this.requestGeneration
    this.setData({ loading: true, errorMessage: '' })
    try {
      const detail = await cleanRecordDetail(this.cleanRecordNo, false)
      if (generation !== this.requestGeneration) return
      const effectiveSource = {
        DEVICE_RECALCULATED: '设备事实重算值',
        MANUAL_SET: '后台人工设置值',
        MANUAL_CLEARED: '后台人工清空',
      }[detail.effective.source]
      const detection = detail.postCleanDetection
      this.setData({
        recordNo: detail.cleanRecordNo,
        resultText: detail.resultKind === 'NORMAL' ? '正常完成' : '系统异常',
        resultTone: detail.resultKind === 'NORMAL' ? 'normal' : 'warning',
        completedAtText: formatLocalDateTime(detail.source.completedAt),
        deviceCompletedAtText: formatLocalDateTime(
          detail.source.deviceCompletedAt,
        ),
        backendReceivedAtText: formatLocalDateTime(
          detail.source.backendReceivedAt,
        ),
        deploymentCode: detail.source.deploymentCode,
        portText: `${detail.source.portNo} 号投口`,
        operationUid: detail.source.operationUid,
        eventUid: detail.source.eventUid,
        commandUid: detail.source.commandUid,
        configVersionText: `第 ${detail.source.cleanConfigVersionNo} 版`,
        removedBagQr: detail.bags.removedBagQr || '—',
        removedBagStateText: detail.bags.removedBagBindingState === 'BOUND'
          ? '清运前已绑定'
          : '清运前未绑定',
        installedBagQr: detail.bags.installedBagQr,
        effectiveWeightText: kg(detail.effective.removedNetWeightKg),
        effectiveSourceText: effectiveSource,
        includedInStatisticsText:
          detail.effective.includedInKnownWeightStatistics ? '计入' : '不计入',
        recordRemark: detail.effective.recordRemark || '无',
        preUnlockText:
          `${kg(detail.weights.preUnlockWeightKg)} · ${
            factStatus(detail.weights.preUnlockStatus)
          }`,
        oldBaselineText:
          `${kg(detail.weights.oldBaselineWeightKg)} · ${
            factStatus(detail.weights.oldBaselineState)
          }`,
        deviceRemovedText:
          `${kg(detail.weights.deviceRemovedNetWeightKg)} · ${
            factStatus(detail.weights.deviceRemovedNetWeightStatus)
          }`,
        recalculatedText:
          `${kg(detail.weights.recalculatedRemovedNetWeightKg)} · ${
            factStatus(detail.weights.recalculatedRemovedNetWeightStatus)
          }`,
        finalTotalText:
          `${kg(detail.weights.finalTotalWeightKg)} · ${
            factStatus(detail.weights.finalTotalWeightStatus)
          }`,
        candidateBaselineText: kg(
          detail.weights.candidateNewBaselineWeightKg,
        ),
        newBaselineText: detail.newBaseline.established
          ? `${kg(detail.newBaseline.baselineWeightKg)} · 已建立`
          : '未建立有效新基准',
        detectionText: detection
          ? `${detection.status}${
            detection.finalResult ? ` · ${detection.finalResult}` : ''
          }${detection.failureCode ? ` · ${detection.failureCode}` : ''}`
          : '尚未建立清运后检测',
        anomalies: detail.anomalies.map((item) => ({
          ...item,
          timeText: formatLocalDateTime(item.detectedAt),
        })),
        photos: detail.photos.map((item) => ({
          ...item,
          positionText: PHOTO_POSITION_TEXT[item.position],
          statusText: PHOTO_STATUS_TEXT[item.status],
        })),
      })
    } catch (error) {
      if (generation !== this.requestGeneration) return
      this.setData({
        errorMessage:
          error instanceof MiniappApiProblem && error.status === 404
            ? '清运记录不存在或无权查看'
            : '清运记录详情暂时无法加载',
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

  onPreviewPhoto(event: WechatMiniprogram.TouchEvent) {
    const current = String(event.currentTarget.dataset.url || '')
    if (!current) return
    const urls = this.data.photos
      .filter((item) => item.status === 'AVAILABLE' && !!item.url)
      .map((item) => item.url as string)
    if (urls.length) wx.previewImage({ current, urls })
  },
})
