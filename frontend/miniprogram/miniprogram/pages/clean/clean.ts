import { deviceList, deviceDoors } from '../../api/device'
import { openClean, myCleans } from '../../api/clean'
import { bindCurrentPhone } from '../../api/auth'
import { requireEntryMode } from '../../utils/guard'
import { getSession, markPhoneBound } from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'
import type { Device, Door, CleanOrder } from '../../types/api'

interface CleanRow extends CleanOrder {
  /** 展示用：实际清运量（净重，回退 weight） */
  netText: string
}

Page({
  phoneBindingIntentKey: '',

  data: {
    devices: [] as Device[],
    deviceNames: [] as string[],
    deviceIndex: -1,
    doors: [] as Door[],
    doorLabels: [] as string[],
    doorIndex: -1,
    bagNo: '',
    opening: false,
    cleans: [] as CleanRow[],
    phoneBound: false,
    phoneGrantSubmitting: false,
  },

  onLoad() {
    if (!requireEntryMode(['CLEANING'])) return
    this.loadDevices()
    this.loadCleans()
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) (tabBar as any).init()
    const session = getSession()
    this.setData({
      phoneBound: session?.phoneBound ?? false,
    })
  },

  async ensurePhoneBindingIntent() {
    if (!this.phoneBindingIntentKey) {
      this.phoneBindingIntentKey = await createIdempotencyKey()
    }
  },

  async onGetPhoneNumber(
    event: WechatMiniprogram.CustomEvent<{
      code?: string
      errMsg?: string
    }>,
  ) {
    if (this.data.phoneGrantSubmitting) return
    const code = event.detail.code
    if (!code) {
      wx.showToast({ title: '已取消手机号授权', icon: 'none' })
      return
    }
    await this.ensurePhoneBindingIntent()
    this.setData({ phoneGrantSubmitting: true })
    try {
      await bindCurrentPhone(code, this.phoneBindingIntentKey)
      markPhoneBound()
      this.setData({ phoneBound: true })
      wx.showToast({ title: '手机号已验证', icon: 'success' })
    } finally {
      this.setData({ phoneGrantSubmitting: false })
    }
  },

  async loadDevices() {
    try {
      const res = await deviceList(1, 100)
      this.setData({
        devices: res.records,
        deviceNames: res.records.map((d) => d.name || d.sn),
      })
    } catch (e) {
      /* request 已 toast */
    }
  },

  async loadCleans() {
    try {
      const res = await myCleans(1, 20)
      this.setData({
        cleans: res.records.map((o) => ({
          ...o,
          netText: o.netWeight ?? o.weight ?? '0.00',
        })),
      })
    } catch (e) {
      /* ignore */
    }
  },

  /** 选择设备 → 加载其投口 */
  onSelectDevice(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.detail.value)
    const device = this.data.devices[index]
    this.setData({ deviceIndex: index, doors: [], doorLabels: [], doorIndex: -1 })
    if (device) this.loadDoors(device.id)
  },

  async loadDoors(deviceId: number) {
    try {
      const doors = await deviceDoors(deviceId)
      this.setData({
        doors,
        doorLabels: doors.map((d) => d.name || `投口${d.doorIndex}`),
      })
    } catch (e) {
      /* ignore */
    }
  },

  onSelectDoor(e: WechatMiniprogram.PickerChange) {
    this.setData({ doorIndex: Number(e.detail.value) })
  },

  /** 扫新空垃圾袋二维码，获取垃圾袋编号 */
  onScanBag() {
    wx.scanCode({
      scanType: ['qrCode', 'barCode'],
      success: (res) => {
        const bagNo = this.parseBagNo(res.result)
        if (!bagNo) {
          wx.showToast({ title: '二维码无法识别垃圾袋', icon: 'none' })
          return
        }
        this.setData({ bagNo })
      },
      fail: () => {
        /* 用户取消扫码，忽略 */
      },
    })
  },

  /** 开发期：手动输入垃圾袋编号 */
  onManualBag() {
    wx.showModal({
      title: '输入垃圾袋编号',
      editable: true,
      placeholderText: '请输入垃圾袋编号（开发测试用）',
      success: (res) => {
        if (!res.confirm) return
        const bagNo = this.parseBagNo(res.content)
        if (!bagNo) {
          wx.showToast({ title: '请输入垃圾袋编号', icon: 'none' })
          return
        }
        this.setData({ bagNo })
      },
    })
  },

  /** 从扫码结果解析垃圾袋编号：支持纯文本或含 bagNo 参数的 URL */
  parseBagNo(raw: string): string {
    if (!raw) return ''
    const m = raw.match(/bagNo=([^&]+)/)
    if (m) return decodeURIComponent(m[1]).trim()
    return raw.trim()
  },

  async onOpen() {
    const { deviceIndex, doorIndex, devices, doors, bagNo } = this.data
    const device = devices[deviceIndex]
    const door = doorIndex >= 0 ? doors[doorIndex] : undefined
    if (!device) {
      wx.showToast({ title: '请选择设备', icon: 'none' })
      return
    }
    if (!door) {
      wx.showToast({ title: '请选择投口', icon: 'none' })
      return
    }
    if (!bagNo) {
      wx.showToast({ title: '请先扫描新垃圾袋', icon: 'none' })
      return
    }

    this.setData({ opening: true })
    wx.showLoading({ title: '开清运门中...' })
    try {
      await openClean(door.id, bagNo)
      wx.hideLoading()
      wx.showModal({
        title: '清运门已开启',
        content: '请清运并换上新垃圾袋，重量由设备自动称重上报，稍后下拉刷新查看',
        showCancel: false,
      })
      this.setData({ bagNo: '' })
      this.loadCleans()
    } catch (e) {
      wx.hideLoading()
      /* request 已 toast */
    } finally {
      this.setData({ opening: false })
    }
  },
})
