import { migrateLegacyMiniappCredentials } from './utils/legacy-session-migration'
import {
  captureOrdinaryDeviceEntry,
  dismissPendingDeviceEntry,
  markPendingDeviceIdentitySelected,
  peekPendingDeviceEntry,
  routePendingDeviceEntry,
} from './utils/device-entry-intent'
import {
  ensureLoggedIn,
  getSession,
  isSessionExpired,
  isSilentLoginSuppressed,
  routeToEntry,
} from './utils/auth'

let bootstrapping: Promise<void> | undefined
let bootstrapSequence = 0
let activeBootstrapEntryId: string | undefined

function errorCode(error: unknown): string {
  if (!error || typeof error !== 'object') return ''
  const code = (error as { code?: unknown }).code
  return typeof code === 'string' ? code : ''
}

async function bootstrapIdentity(sequence: number): Promise<void> {
  const pending = peekPendingDeviceEntry()
  const current = getSession()

  if (pending) {
    if (
      current
      && !isSessionExpired(current)
      && pending.selectedOrganizationUserUid === current.organizationUserUid
    ) {
      if (sequence !== bootstrapSequence) return
      routePendingDeviceEntry(current)
      return
    }
    try {
      const selected = await ensureLoggedIn({ deviceCode: pending.deviceCode })
      if (sequence !== bootstrapSequence) return
      markPendingDeviceIdentitySelected(
        pending.entryId,
        selected.organizationUserUid,
      )
      if (!routePendingDeviceEntry(selected)) routeToEntry(selected)
    } catch (error) {
      if (sequence !== bootstrapSequence) return
      if (errorCode(error) === 'IDENTITY.DEVICE_ENTRY_INVALID') {
        dismissPendingDeviceEntry(pending.entryId)
        wx.showToast({ title: '设备二维码当前不可用', icon: 'none' })
        return
      }
      console.warn('[miniapp-auth] 扫码登录暂未完成', error)
    }
    return
  }

  if (current && !isSessionExpired(current)) return
  if (isSilentLoginSuppressed()) return

  try {
    const session = await ensureLoggedIn()
    if (sequence !== bootstrapSequence) return
    routeToEntry(session)
  } catch (error) {
    if (sequence !== bootstrapSequence) return
    // 新用户从普通入口进入时，后端明确要求先扫码注册；这不是页面错误，
    // 首页继续保持游客态即可。
    if (errorCode(error) !== 'IDENTITY.DEVICE_REGISTRATION_REQUIRED') {
      console.warn('[miniapp-auth] 静默登录暂未完成', error)
    }
  }
}

function startIdentityBootstrap(): void {
  const pendingEntryId = peekPendingDeviceEntry()?.entryId
  if (bootstrapping && pendingEntryId === activeBootstrapEntryId) return

  const sequence = ++bootstrapSequence
  activeBootstrapEntryId = pendingEntryId
  let tracked: Promise<void>
  tracked = bootstrapIdentity(sequence).finally(() => {
    if (bootstrapping !== tracked) return
    bootstrapping = undefined
    activeBootstrapEntryId = undefined
  })
  bootstrapping = tracked
}

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
    startIdentityBootstrap()
  },
})
