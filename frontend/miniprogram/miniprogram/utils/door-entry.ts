import { test } from '../config/index'

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

function showResolved(doorId: number) {
  wx.showModal({
    title: '投口识别成功',
    content: `已识别投口 ID：${doorId}\n\n当前为静态演示模式，未发送真实开门指令。`,
    showCancel: false,
    confirmText: '我知道了',
  })
}

function resolve(raw: string, invalidText: string) {
  const doorId = parseDoorId(raw)
  if (doorId == null) {
    wx.showToast({ title: invalidText, icon: 'none' })
    return
  }
  showResolved(doorId)
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
