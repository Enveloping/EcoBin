import { migrateLegacyMiniappCredentials } from './utils/legacy-session-migration'

App<IAppOption>({
  globalData: {
    session: undefined,
  },
  onLaunch() {
    migrateLegacyMiniappCredentials(wx)
  },
})
