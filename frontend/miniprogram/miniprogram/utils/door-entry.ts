import { test } from '../config/index'
import { ensureLoggedIn, routeToEntry } from './auth'
import {
  captureScannedDeviceEntry,
  dismissPendingDeviceEntry,
  markPendingDeviceIdentitySelected,
  routePendingDeviceEntry,
} from './device-entry-intent'

function errorCode(error: unknown): string {
  if (!error || typeof error !== 'object') return ''
  const code = (error as { code?: unknown }).code
  return typeof code === 'string' ? code : ''
}

async function acceptDeviceLink(rawLink: string): Promise<void> {
  const entry = captureScannedDeviceEntry(rawLink)
  if (!entry) {
    wx.showToast({
      title: '二维码不是有效的 EcoBin 设备码',
      icon: 'none',
    })
    return
  }
  wx.showLoading({ title: '正在识别设备', mask: true })
  try {
    // 扫码永远是明确的机构选择，不能继续沿用当前机构会话。
    const session = await ensureLoggedIn({ deviceCode: entry.deviceCode })
    markPendingDeviceIdentitySelected(entry.entryId, session.organizationUserUid)
    if (!routePendingDeviceEntry(session)) routeToEntry(session)
  } catch (error) {
    if (errorCode(error) === 'IDENTITY.DEVICE_ENTRY_INVALID') {
      dismissPendingDeviceEntry(entry.entryId)
    }
    const message = error instanceof Error && error.message
      ? error.message
      : '暂时无法识别设备'
    wx.showToast({ title: message, icon: 'none' })
  } finally {
    wx.hideLoading()
  }
}

function scanDevice(): void {
  wx.scanCode({
    scanType: ['qrCode'],
    success: (result) => void acceptDeviceLink(result.result),
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
      if (result.confirm) void acceptDeviceLink(result.content || '')
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
