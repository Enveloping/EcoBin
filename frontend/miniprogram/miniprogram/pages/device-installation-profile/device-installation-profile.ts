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

function locationAddress(address: string, name: string): string {
  const normalizedAddress = String(address || '').trim()
  const normalizedName = String(name || '').trim()
  if (!normalizedName || normalizedAddress.includes(normalizedName)) {
    return normalizedAddress || normalizedName
  }
  return [normalizedAddress, normalizedName].filter(Boolean).join(' ')
}

const MAX_ACCEPTABLE_LOCATION_ACCURACY_METERS = 100

function locationAccuracyMeters(
  result: WechatMiniprogram.GetLocationSuccessCallbackResult,
): number | null {
  const horizontalAccuracy = Number(result.horizontalAccuracy)
  if (Number.isFinite(horizontalAccuracy) && horizontalAccuracy >= 0) {
    return horizontalAccuracy
  }
  const accuracy = Number(result.accuracy)
  return Number.isFinite(accuracy) && accuracy >= 0 ? accuracy : null
}

function locationPermissionDenied(error: WechatMiniprogram.GeneralCallbackResult) {
  const message = String(error.errMsg || '').toLowerCase()
  return message.includes('auth deny')
    || message.includes('auth denied')
    || message.includes('permission denied')
    || message.includes('scope.userlocation')
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
    locating: false,
    locationAccuracyText: '',
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
      locationAccuracyText: '',
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

  onLocateCurrentPosition() {
    if (this.data.locating) return
    this.setData({ locating: true })
    wx.getLocation({
      type: 'gcj02',
      isHighAccuracy: true,
      highAccuracyExpireTime: 8000,
      success: result => {
        const accuracyMeters = locationAccuracyMeters(result)
        if (accuracyMeters !== null
          && accuracyMeters > MAX_ACCEPTABLE_LOCATION_ACCURACY_METERS) {
          wx.showModal({
            title: '定位精度较低',
            content: `当前位置误差约 ${Math.ceil(accuracyMeters)} 米，未更新设备坐标。请在手机系统中开启精确位置，或到室外后重试。`,
            showCancel: false,
          })
          return
        }
        this.setData({
          longitude: coordinate(result.longitude),
          latitude: coordinate(result.latitude),
          hasLocation: true,
          locationAccuracyText: accuracyMeters === null
            ? '本次定位精度未知'
            : `本次定位误差约 ${Math.max(1, Math.ceil(accuracyMeters))} 米`,
        })
        wx.showToast({ title: '已获取当前位置', icon: 'success' })
      },
      fail: error => {
        if (locationPermissionDenied(error)) {
          wx.showModal({
            title: '无法获取位置',
            content: '请允许小程序使用位置信息，并在手机系统中为微信开启精确位置。',
            confirmText: '去设置',
            success: result => {
              if (result.confirm) wx.openSetting()
            },
          })
          return
        }
        wx.showToast({ title: '手机定位失败，请重试', icon: 'none' })
      },
      complete: () => this.setData({ locating: false }),
    })
  },

  onChooseLocation() {
    if (this.data.locating || !this.data.hasLocation) return
    const options: WechatMiniprogram.ChooseLocationOption = {
      latitude: Number(this.data.latitude),
      longitude: Number(this.data.longitude),
      success: result => {
        this.setData({
          address: locationAddress(result.address, result.name),
          longitude: coordinate(result.longitude),
          latitude: coordinate(result.latitude),
          hasLocation: true,
          locationAccuracyText: '已在地图中确认位置',
        })
      },
      fail: error => {
        if (!String(error.errMsg || '').includes('cancel')) {
          wx.showToast({ title: '地图位置选择失败', icon: 'none' })
        }
      },
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
      wx.showToast({ title: '请先获取手机当前位置', icon: 'none' })
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
