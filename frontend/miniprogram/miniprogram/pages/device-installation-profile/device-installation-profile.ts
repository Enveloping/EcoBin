import {
  deviceInstallationProfile,
  updateDeviceInstallationProfile,
} from '../../api/clean'
import type { DeviceInstallationProfile } from '../../types/api'
import { parseCleaningDeviceCode } from '../../utils/clean-operation-intent'
import { requireEntryMode } from '../../utils/guard'
import { formatLocalDateTime } from '../../utils/local-time'
import { MiniappApiProblem } from '../../utils/request'

function decodeDeviceCode(value?: string): string {
  if (!value) return ''
  try {
    return parseCleaningDeviceCode(decodeURIComponent(value))
  } catch {
    return ''
  }
}

function coordinate(value: number | string): string {
  const numeric = Number(value)
  const normalized = Object.is(numeric, -0) ? 0 : numeric
  return normalized.toFixed(7)
}

function locationAddress(
  address: string,
  name: string,
): string {
  const normalizedAddress = String(address || '').trim()
  const normalizedName = String(name || '').trim()
  if (!normalizedName || normalizedAddress.includes(normalizedName)) {
    return normalizedAddress || normalizedName
  }
  return [normalizedAddress, normalizedName].filter(Boolean).join(' ')
}

Page({
  loadRequest: undefined as Promise<DeviceInstallationProfile> | undefined,
  saveRequest: undefined as Promise<DeviceInstallationProfile> | undefined,

  data: {
    deviceCode: '',
    version: 0,
    displayName: '',
    address: '',
    longitude: '',
    latitude: '',
    updatedText: '',
    loading: true,
    saving: false,
    errorMessage: '',
    hasLocation: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    if (!requireEntryMode(['CLEANING'])) return
    const deviceCode = decodeDeviceCode(options.deviceCode)
    if (!deviceCode) {
      wx.showToast({ title: '设备公开码无效', icon: 'none' })
      wx.navigateBack()
      return
    }
    this.setData({ deviceCode })
    void this.load()
  },

  onPullDownRefresh() {
    void this.load().finally(() => wx.stopPullDownRefresh())
  },

  load(): Promise<void> {
    if (this.loadRequest) {
      return this.loadRequest.then(() => undefined)
    }
    this.setData({ loading: true, errorMessage: '' })
    const request = deviceInstallationProfile(this.data.deviceCode, false)
    this.loadRequest = request
    return request.then(
      profile => this.applyProfile(profile),
      () => this.setData({
        errorMessage: '设备安装资料暂时无法加载，请检查网络后重试。',
      }),
    ).finally(() => {
      if (this.loadRequest === request) this.loadRequest = undefined
      this.setData({ loading: false })
    })
  },

  applyProfile(profile: DeviceInstallationProfile) {
    this.setData({
      version: profile.version,
      displayName: profile.displayName || '',
      address: profile.address || '',
      longitude: profile.longitude || '',
      latitude: profile.latitude || '',
      hasLocation: Boolean(profile.longitude && profile.latitude),
      updatedText: profile.updatedAt
        ? formatLocalDateTime(profile.updatedAt)
        : '尚未保存',
    })
  },

  onNameInput(event: WechatMiniprogram.Input) {
    this.setData({ displayName: String(event.detail.value || '') })
  },

  onAddressInput(event: WechatMiniprogram.Input) {
    this.setData({ address: String(event.detail.value || '') })
  },

  onChooseLocation() {
    const latitude = Number(this.data.latitude)
    const longitude = Number(this.data.longitude)
    const options: WechatMiniprogram.ChooseLocationOption = {
      success: result => {
        this.setData({
          address: locationAddress(result.address, result.name),
          longitude: coordinate(result.longitude),
          latitude: coordinate(result.latitude),
          hasLocation: true,
        })
      },
      fail: error => {
        if (!String(error.errMsg || '').includes('cancel')) {
          wx.showToast({ title: '地图位置选择失败', icon: 'none' })
        }
      },
    }
    if (this.data.hasLocation
      && Number.isFinite(latitude)
      && Number.isFinite(longitude)) {
      options.latitude = latitude
      options.longitude = longitude
    }
    wx.chooseLocation(options)
  },

  onRetry() {
    void this.load()
  },

  onSave() {
    if (this.saveRequest) return
    const displayName = this.data.displayName.trim()
    const address = this.data.address.trim()
    if (!displayName) {
      wx.showToast({ title: '请填写设备名称', icon: 'none' })
      return
    }
    if (!this.data.hasLocation
      || !this.data.longitude
      || !this.data.latitude) {
      wx.showToast({ title: '请先在地图中选择位置', icon: 'none' })
      return
    }
    if (!address) {
      wx.showToast({ title: '请填写详细安装地址', icon: 'none' })
      return
    }

    this.setData({ saving: true })
    const request = updateDeviceInstallationProfile(
      this.data.deviceCode,
      {
        expectedVersion: this.data.version,
        displayName,
        address,
        longitude: this.data.longitude,
        latitude: this.data.latitude,
      },
      false,
    )
    this.saveRequest = request
    void request.then(
      profile => {
        this.applyProfile(profile)
        this.getOpenerEventChannel().emit(
          'installationProfileUpdated', profile)
        wx.navigateBack({
          success: () => wx.showToast({ title: '设备信息已保存' }),
        })
      },
      error => {
        if (error instanceof MiniappApiProblem
          && error.code === 'COMMON.VERSION_CONFLICT') {
          wx.showModal({
            title: '资料已更新',
            content: '其他清运员刚刚修改了这台设备，请刷新后再保存。',
            showCancel: false,
            success: () => void this.load(),
          })
          return
        }
        wx.showToast({ title: '保存失败，请稍后重试', icon: 'none' })
      },
    ).finally(() => {
      if (this.saveRequest === request) this.saveRequest = undefined
      this.setData({ saving: false })
    })
  },
})
