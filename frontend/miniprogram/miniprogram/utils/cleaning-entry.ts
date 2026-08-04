import { getEntryMode } from './auth'
import { parseCleaningDeploymentCode } from './clean-operation-intent'

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
      const deploymentCode = parseCleaningDeploymentCode(result)
      if (!deploymentCode) {
        wx.showToast({ title: '未识别到设备二维码', icon: 'none' })
        return
      }
      wx.navigateTo({
        url: `/pages/clean-operation/clean-operation?deploymentCode=${encodeURIComponent(deploymentCode)}`,
      })
    },
    fail: ({ errMsg }) => {
      if (!/cancel/i.test(errMsg)) {
        wx.showToast({ title: '扫码失败，请重试', icon: 'none' })
      }
    },
  })
}
