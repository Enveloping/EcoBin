import { migrateLegacyMiniappCredentials } from './utils/legacy-session-migration'
import { captureOrdinaryDeviceEntry } from './utils/device-entry-intent'

App<IAppOption>({
  globalData: {
    session: undefined,
    testViewMode: undefined,
  },
  onLaunch() {
    migrateLegacyMiniappCredentials(wx)
  },
  onShow() {
    captureOrdinaryDeviceEntry(wx.getEnterOptionsSync())
  },
})
