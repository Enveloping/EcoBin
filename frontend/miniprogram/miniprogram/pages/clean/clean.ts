import { requireEntryMode } from '../../utils/guard'

type DeviceFilter =
  | 'ALL'
  | 'ONLINE'
  | 'NO_DELIVERY_24H'
  | 'NO_CLEAN_24H'
  | 'FULL'
  | 'FULL_TIMEOUT_2H'

const FILTER_TITLES: Record<DeviceFilter, string> = {
  ALL: '全部设备',
  ONLINE: '在线设备',
  NO_DELIVERY_24H: '一天未投递设备',
  NO_CLEAN_24H: '一天未清运设备',
  FULL: '满溢设备',
  FULL_TIMEOUT_2H: '满溢超时2h',
}

function isDeviceFilter(value: string): value is DeviceFilter {
  return Object.prototype.hasOwnProperty.call(FILTER_TITLES, value)
}

Page({
  data: {
    statusBarHeight: wx.getSystemInfoSync().statusBarHeight || 24,
  },

  onLoad() {
    requireEntryMode(['CLEANING'])
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
  },

  onCardTap(event: WechatMiniprogram.TouchEvent) {
    const filter = String(event.currentTarget.dataset.filter || '')
    if (filter === 'RECORDS') {
      wx.navigateTo({ url: '/pages/clean-records/clean-records' })
      return
    }
    if (!isDeviceFilter(filter)) return

    wx.navigateTo({
      url:
        `/pages/clean-devices/clean-devices?filter=${filter}`
        + `&title=${encodeURIComponent(FILTER_TITLES[filter])}`,
    })
  },
})
