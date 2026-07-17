import { test } from '../config/index'
import { openDoor } from '../api/delivery'
import { ensureLoggedIn } from './auth'

let opening = false

export function parseDoorId(raw: string): number | null {
  const value = (raw || '').trim()
  if (!value) return null
  if (/^\d+$/.test(value)) {
    const direct = Number(value)
    return Number.isInteger(direct) && direct > 0 ? direct : null
  }
  const match = value.match(/[?&#]doorId=(\d+)/i)
  if (!match) return null
  const id = Number(match[1])
  return Number.isInteger(id) && id > 0 ? id : null
}

function showOpened(doorId: number) {
  wx.showModal({
    title: '开门请求已发送',
    content: `投口 ID：${doorId}\n\n请在设备端确认开门并完成投递。投递订单将在设备完成称重并上报后生成。`,
    showCancel: false,
    confirmText: '我知道了',
  })
}

function showOpenError(error: unknown) {
  if (error instanceof Error && error.message === 'unauthorized') return
  const message = error instanceof Error && error.message ? error.message : '开门请求失败，请稍后重试'
  wx.showToast({ title: message, icon: 'none' })
}

async function requestOpenDoor(doorId: number) {
  if (opening) {
    wx.showToast({ title: '开门请求正在处理中', icon: 'none' })
    return
  }

  opening = true
  wx.showLoading({ title: '正在发送请求', mask: true })
  try {
    await ensureLoggedIn()
    await openDoor(doorId, false)
    wx.hideLoading()
    showOpened(doorId)
  } catch (error) {
    wx.hideLoading()
    showOpenError(error)
  } finally {
    opening = false
  }
}

function resolve(raw: string, invalidText: string) {
  const doorId = parseDoorId(raw)
  if (doorId == null) {
    wx.showToast({ title: invalidText, icon: 'none' })
    return
  }
  void requestOpenDoor(doorId)
}

function scanDoor() {
  wx.scanCode({
    scanType: ['qrCode', 'barCode'],
    success: (res) => resolve(res.result, '二维码无法识别投口'),
    fail: (error) => {
      if (!error.errMsg || error.errMsg.indexOf('cancel') < 0) {
        wx.showToast({ title: '未能完成扫码', icon: 'none' })
      }
    },
  })
}

function enterDoorId() {
  wx.showModal({
    title: '填写投口 ID',
    editable: true,
    placeholderText: '请输入正整数投口 ID',
    confirmText: '确认',
    success: (res) => {
      if (res.confirm) resolve(res.content || '', '请输入正确的投口 ID')
    },
  })
}

export function startDoorEntry() {
  if (!test) {
    scanDoor()
    return
  }
  wx.showActionSheet({
    itemList: ['扫码识别', '填写投口 ID'],
    success: (res) => {
      if (res.tapIndex === 0) scanDoor()
      if (res.tapIndex === 1) enterDoorId()
    },
  })
}
