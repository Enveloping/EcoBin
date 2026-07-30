import { requireEntryMode } from '../../utils/guard'
import { isCrossIdentityPreview } from '../../utils/test-entry-preview'

const FILTERS = new Set([
  'ALL',
  'ONLINE',
  'NO_DELIVERY_24H',
  'NO_CLEAN_24H',
  'FULL',
  'FULL_TIMEOUT_2H',
])

Page({
  data: {
    title: '设备列表',
    filter: '',
    description: '清运设备列表接口正在接入，接入后将在这里显示对应设备。',
  },

  onLoad(options: Record<string, string | undefined>) {
    if (!requireEntryMode(['CLEANING'])) return
    const filter = String(options.filter || '')
    const title = this.decodeTitle(options.title)
    if (!FILTERS.has(filter)) {
      wx.showToast({ title: '设备筛选条件无效', icon: 'none' })
      return
    }
    this.setData({
      filter,
      title,
      description: isCrossIdentityPreview()
        ? '当前为清运端界面预览，登录账号权限未改变，因此不会请求清运设备数据。'
        : '清运设备列表接口正在接入，接入后将在这里显示对应设备。',
    })
    wx.setNavigationBarTitle({ title })
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
