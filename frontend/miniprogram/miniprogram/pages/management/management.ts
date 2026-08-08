import { getSession, logout } from '../../utils/auth'
import { requireEntryMode } from '../../utils/guard'
import {
  ENTRY_PREVIEW_NOTICE,
  isCrossIdentityPreview,
  isEntryPreviewEnabled,
  showEntryPreviewSwitcher,
} from '../../utils/test-entry-preview'

Page({
  data: {
    organizationName: '',
    displayName: '',
    capabilities: [] as string[],
    entryPreviewEnabled: false,
    previewOnly: false,
    previewNotice: ENTRY_PREVIEW_NOTICE,
  },

  onLoad() {
    if (!requireEntryMode(['MANAGEMENT'])) return
    const session = getSession()
    if (!session) return
    this.setData({
      organizationName: session.organization.displayName,
      displayName: session.displayName,
      capabilities: session.capabilities,
      entryPreviewEnabled: isEntryPreviewEnabled(),
      previewOnly: isCrossIdentityPreview(),
    })
  },

  onEntryPreview() {
    showEntryPreviewSwitcher()
  },

  onSwitchOrganization() {
    wx.navigateTo({ url: '/pages/account-switcher/account-switcher' })
  },

  onLogout() {
    logout()
  },
})
