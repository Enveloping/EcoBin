import { getEntryMode } from '../../utils/auth'
import { requireEntryMode } from '../../utils/guard'

Page({
  data: {
    deploymentCode: '',
    previewOnly: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    if (!requireEntryMode(['CLEANING'])) return
    const deploymentCode = this.decodeDeploymentCode(options.deploymentCode)
    this.setData({
      deploymentCode,
      previewOnly: getEntryMode() !== 'CLEANING',
    })
  },

  decodeDeploymentCode(value?: string): string {
    if (!value) return ''
    try {
      return decodeURIComponent(value)
    } catch {
      return ''
    }
  },
})
