import { myCleanDevices } from '../../api/clean'
import { FEATURES } from '../../config/index'
import type {
  CleanDeviceFilter,
  CleanDeviceItem,
  DeviceInstallationProfile,
} from '../../types/api'
import { requireEntryMode } from '../../utils/guard'
import { startCleaningEntry } from '../../utils/cleaning-entry'
import { formatLocalDateTime } from '../../utils/local-time'
import { MiniappApiProblem } from '../../utils/request'

interface CleanDeviceView extends CleanDeviceItem {
  nameText: string
  addressText: string
  connectionText: string
  connectionClass: string
  lastDeliveryText: string
  lastCleanText: string
  oldestFullText: string
}

const FILTERS = new Set<CleanDeviceFilter>([
  'ALL',
  'ONLINE',
  'NO_DELIVERY_24H',
  'NO_CLEAN_24H',
  'FULL',
  'FULL_TIMEOUT_2H',
])
const PAGE_SIZE = 20

function isFilter(value: string): value is CleanDeviceFilter {
  return FILTERS.has(value as CleanDeviceFilter)
}

function deviceView(item: CleanDeviceItem): CleanDeviceView {
  const connection = item.connectionStatus === 'ONLINE'
    ? ['在线', 'online']
    : item.connectionStatus === 'OFFLINE'
      ? ['离线', 'offline']
      : ['状态未知', 'unknown']
  return {
    ...item,
    nameText: item.displayName || item.deviceCode,
    addressText: item.address || '暂未设置安装地址',
    connectionText: connection[0],
    connectionClass: connection[1],
    lastDeliveryText: item.lastDeliveryAt
      ? formatLocalDateTime(item.lastDeliveryAt)
      : '从未投递',
    lastCleanText: item.lastCleanAt
      ? formatLocalDateTime(item.lastCleanAt)
      : '从未清运',
    oldestFullText: item.oldestFullSince
      ? formatLocalDateTime(item.oldestFullSince)
      : '—',
  }
}

Page({
  requestGeneration: 0,
  cursorRecoveryUsed: false,

  data: {
    title: '设备列表',
    filter: 'ALL' as CleanDeviceFilter,
    serviceUnavailable: !FEATURES.targetCleaningDataApi,
    devices: [] as CleanDeviceView[],
    nextCursor: null as string | null,
    loading: false,
    initialLoading: false,
    finished: false,
    errorMessage: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    if (!requireEntryMode(['CLEANING'])) return
    const requestedFilter = String(options.filter || 'ALL')
    if (!isFilter(requestedFilter)) {
      wx.showToast({ title: '设备筛选条件无效', icon: 'none' })
      return
    }
    const title = this.decodeTitle(options.title)
    this.setData({
      filter: requestedFilter,
      title,
      initialLoading: FEATURES.targetCleaningDataApi,
      finished: !FEATURES.targetCleaningDataApi,
    })
    wx.setNavigationBarTitle({ title })
    if (FEATURES.targetCleaningDataApi) void this.reload()
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
    if (!preserveCursorRecovery) this.cursorRecoveryUsed = false
    const generation = ++this.requestGeneration
    this.setData({
      devices: [],
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
      const page = await myCleanDevices({
        filter: this.data.filter,
        cursor,
        limit: PAGE_SIZE,
      }, false)
      if (activeGeneration !== this.requestGeneration) return
      const nextRows = page.items.map(deviceView)
      this.setData({
        devices: cursor ? this.data.devices.concat(nextRows) : nextRows,
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
      } else if (this.data.devices.length === 0) {
        this.setData({ errorMessage: '设备列表暂时无法加载' })
      } else {
        wx.showToast({ title: '更多设备加载失败', icon: 'none' })
      }
    } finally {
      if (activeGeneration === this.requestGeneration) {
        this.setData({ loading: false, initialLoading: false })
      }
    }
    if (recoverInvalidCursor && activeGeneration === this.requestGeneration) {
      wx.showToast({ title: '设备状态已更新', icon: 'none' })
      await this.reload(undefined, true)
    }
  },

  onRetry() {
    void this.reload()
  },

  onContinueLoading() {
    void this.loadMore()
  },

  onScanStart() {
    startCleaningEntry()
  },

  onConfigureInstallation(event: WechatMiniprogram.TouchEvent) {
    const deviceCode = String(
      event.currentTarget.dataset.deviceCode || '',
    )
    if (!deviceCode) return
    wx.navigateTo({
      url: `/pages/device-installation-profile/device-installation-profile?deviceCode=${
        encodeURIComponent(deviceCode)
      }`,
      events: {
        installationProfileUpdated: (
          profile: DeviceInstallationProfile,
        ) => {
          this.setData({
            devices: this.data.devices.map(item => item.deviceCode
              === profile.deviceCode
              ? deviceView({
                ...item,
                displayName: profile.displayName,
                address: profile.address,
              })
              : item),
          })
        },
      },
    })
  },

  decodeTitle(value?: string): string {
    if (!value) return '设备列表'
    try {
      return decodeURIComponent(value)
    } catch {
      return '设备列表'
    }
  },
})
