import { getEntryMode } from '../../utils/auth'
import { requireEntryMode } from '../../utils/guard'

Page({
  data: {
    deviceCode: '',
    previewOnly: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    if (!requireEntryMode(['CLEANING'])) return
    const deviceCode = this.decodeDeviceCode(options.deviceCode)
    this.setData({
      deviceCode,
      previewOnly: getEntryMode() !== 'CLEANING',
    })
  },

  decodeDeviceCode(value?: string): string {
    if (!value) return ''
    try {
      return decodeURIComponent(value)
    } catch {
      return ''
    }
  },
})
