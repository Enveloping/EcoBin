import { test } from '../config/index'
import { getSession } from './auth'
import {
  captureScannedDeviceEntry,
  routePendingDeviceEntry,
} from './device-entry-intent'

function acceptDeviceLink(rawLink: string): void {
  const entry = captureScannedDeviceEntry(rawLink)
  if (!entry) {
    wx.showToast({
      title: '二维码不是有效的 EcoBin 设备码',
      icon: 'none',
    })
    return
  }
  const session = getSession()
  if (!routePendingDeviceEntry(session) && !session) {
    wx.reLaunch({ url: '/pages/login/login' })
  }
}

function scanDevice(): void {
  wx.scanCode({
    scanType: ['qrCode'],
    success: (result) => acceptDeviceLink(result.result),
    fail: (error) => {
      if (!error.errMsg || error.errMsg.indexOf('cancel') < 0) {
        wx.showToast({ title: '未能完成扫码', icon: 'none' })
      }
    },
  })
}

function enterDeviceLink(): void {
  wx.showModal({
    title: '填写设备二维码链接',
    editable: true,
    placeholderText: '粘贴包含 deviceCode 的完整设备链接',
    confirmText: '识别',
    success: (result) => {
      if (result.confirm) acceptDeviceLink(result.content || '')
    },
  })
}

export function startDoorEntry(): void {
  if (!test) {
    scanDevice()
    return
  }
  wx.showActionSheet({
    itemList: ['扫码识别', '填写设备链接'],
    success: (result) => {
      if (result.tapIndex === 0) scanDevice()
      if (result.tapIndex === 1) enterDeviceLink()
    },
  })
}
