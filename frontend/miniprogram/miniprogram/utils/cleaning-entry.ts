import { getEntryMode } from './auth'

function parseDeviceCode(raw: string): string {
  const value = raw.trim()
  if (!value) return ''

  const queryMatch = value.match(/[?&]deviceCode=([^&#]+)/)
  if (queryMatch) {
    try {
      return decodeURIComponent(queryMatch[1]).trim()
    } catch {
      return ''
    }
  }

  return /^Dv_[A-Za-z0-9_-]{24,61}$/.test(value) ? value : ''
}

export function startCleaningEntry(): void {
  if (getEntryMode() !== 'CLEANING') {
    wx.showToast({
      title: '当前仅预览清运端，账号权限未改变',
      icon: 'none',
    })
    return
  }

  wx.scanCode({
    scanType: ['qrCode'],
    success: ({ result }) => {
      const deviceCode = parseDeviceCode(result)
      if (!deviceCode) {
        wx.showToast({ title: '未识别到设备二维码', icon: 'none' })
        return
      }
      wx.navigateTo({
        url: `/pages/clean-operation/clean-operation?deviceCode=${encodeURIComponent(deviceCode)}`,
      })
    },
    fail: ({ errMsg }) => {
      if (!/cancel/i.test(errMsg)) {
        wx.showToast({ title: '扫码失败，请重试', icon: 'none' })
      }
    },
  })
}
