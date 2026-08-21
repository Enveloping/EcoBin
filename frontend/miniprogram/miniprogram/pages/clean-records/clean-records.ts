import { myCleanRecords } from '../../api/clean'
import { FEATURES } from '../../config/index'
import type { CleanRecordItem } from '../../types/api'
import { formatLocalDateTime } from '../../utils/local-time'
import { requireEntryMode } from '../../utils/guard'
import { MiniappApiProblem } from '../../utils/request'

interface CleanRecordView extends CleanRecordItem {
  timeText: string
  weightText: string
  resultText: string
  photoText: string
  removedBagText: string
}

const PAGE_SIZE = 20

function weightText(value: string | null): string {
  return value ? `${value} kg` : '有效重量未知'
}

function recordView(item: CleanRecordItem): CleanRecordView {
  return {
    ...item,
    timeText: formatLocalDateTime(item.deviceCompletedAt),
    weightText: weightText(item.effectiveRemovedNetWeightKg),
    resultText: item.resultKind === 'NORMAL' ? '正常完成' : '系统异常',
    photoText: item.photoCompleteness === 'COMPLETE'
      ? '照片完整'
      : '照片不完整',
    removedBagText: item.removedBagQr || '原投口未绑定袋',
  }
}

Page({
  requestGeneration: 0,
  cursorRecoveryUsed: false,

  data: {
    serviceUnavailable: !FEATURES.targetCleaningDataApi,
    records: [] as CleanRecordView[],
    nextCursor: null as string | null,
    loading: false,
    initialLoading: FEATURES.targetCleaningDataApi,
    finished: !FEATURES.targetCleaningDataApi,
    errorMessage: '',
  },

  onLoad() {
    if (!requireEntryMode(['CLEANING'])) return
    if (!FEATURES.targetCleaningDataApi) {
      this.setData({ initialLoading: false, finished: true })
      return
    }
    void this.reload()
  },

  onPullDownRefresh() {
    if (!FEATURES.targetCleaningDataApi) {
      wx.stopPullDownRefresh()
      return
    }
    void this.reload(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (!FEATURES.targetCleaningDataApi) return
    void this.loadMore()
  },

  onUnload() {
    this.requestGeneration += 1
  },

  async reload(done?: () => void, preserveCursorRecovery = false) {
    if (!FEATURES.targetCleaningDataApi) {
      done?.()
      return
    }
    if (!preserveCursorRecovery) this.cursorRecoveryUsed = false
    const generation = ++this.requestGeneration
    this.setData({
      records: [],
      nextCursor: null,
      loading: false,
      initialLoading: true,
      finished: false,
      errorMessage: '',
    })
    try {
      await this.fetchNext(undefined, generation)
    } finally {
      done?.()
    }
  },

  async loadMore() {
    if (this.data.loading || this.data.finished) return
    await this.fetchNext(this.data.nextCursor ?? undefined)
  },

  async fetchNext(cursor?: string, generation?: number) {
    const activeGeneration = generation ?? this.requestGeneration
    if (activeGeneration !== this.requestGeneration || this.data.loading) return
    this.setData({ loading: true, errorMessage: '' })
    let recoverInvalidCursor = false
    try {
      const page = await myCleanRecords(
        { cursor, limit: PAGE_SIZE },
        false,
      )
      if (activeGeneration !== this.requestGeneration) return
      const nextRows = page.items.map(recordView)
      this.setData({
        records: cursor ? this.data.records.concat(nextRows) : nextRows,
        nextCursor: page.nextCursor,
        finished: !page.nextCursor,
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
      } else if (this.data.records.length === 0) {
        this.setData({ errorMessage: '清运记录暂时无法加载' })
      } else {
        wx.showToast({ title: '更多清运记录加载失败', icon: 'none' })
      }
    } finally {
      if (activeGeneration === this.requestGeneration) {
        this.setData({ loading: false, initialLoading: false })
      }
    }

    if (recoverInvalidCursor && activeGeneration === this.requestGeneration) {
      wx.showToast({ title: '清运记录已更新', icon: 'none' })
      await this.reload(undefined, true)
    }
  },

  onRetry() {
    void this.reload()
  },

  onContinueLoading() {
    void this.loadMore()
  },

  onRecordTap(event: WechatMiniprogram.TouchEvent) {
    const cleanRecordNo = String(
      event.currentTarget.dataset.cleanRecordNo || '',
    )
    if (!cleanRecordNo) return
    wx.navigateTo({
      url: `/pages/clean-record-detail/clean-record-detail?cleanRecordNo=${
        encodeURIComponent(cleanRecordNo)
      }`,
    })
  },
})
