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
import {
  ensureFactorySession,
  FactoryApiProblem,
} from './api/factory'
import {
  captureFactoryBindingEntry,
  forgetFactoryBinding,
  isFactoryBindingKnown,
  isFactoryModeSuppressed,
  isFactoryPage,
  routeToFactoryAcceptance,
} from './utils/factory-mode'

let bootstrapping: Promise<void> | undefined
let bootstrapSequence = 0
let activeBootstrapEntryId: string | undefined

function errorCode(error: unknown): string {
  if (!error || typeof error !== 'object') return ''
  const code = (error as { code?: unknown }).code
  return typeof code === 'string' ? code : ''
}

function factoryBindingRequired(error: unknown): boolean {
  return error instanceof FactoryApiProblem
    && error.code === 'IDENTITY.FACTORY_BINDING_REQUIRED'
}

async function enterFactoryIfBound(sequence: number): Promise<boolean> {
  if (isFactoryModeSuppressed()) return false
  try {
    await ensureFactorySession()
    if (sequence !== bootstrapSequence) return true
    const pending = peekPendingDeviceEntry()
    const deviceCode = pending?.deviceCode
    if (pending) dismissPendingDeviceEntry(pending.entryId)
    routeToFactoryAcceptance(deviceCode)
    return true
  } catch (error) {
    if (sequence !== bootstrapSequence) return true
    if (factoryBindingRequired(error)) {
      forgetFactoryBinding()
      return false
    }
    if (errorCode(error) === 'IDENTITY.FACTORY_OPERATOR_UNAVAILABLE') {
      forgetFactoryBinding()
      return false
    }
    console.warn('[factory-auth] 厂家身份自动识别暂未完成', error)
    // 已知厂家微信在网络不确定时不能降级成用户投递，避免扫描设备码后
    // 因一次探测超时而建立错误的普通用户业务。
    if (!isFactoryBindingKnown()) return false
    const pending = peekPendingDeviceEntry()
    const deviceCode = pending?.deviceCode
    if (pending) dismissPendingDeviceEntry(pending.entryId)
    routeToFactoryAcceptance(deviceCode)
    return true
  }
}

async function bootstrapOrdinaryIdentity(sequence: number): Promise<void> {
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
    // 新用户从普通入口进入时，后端明确要求先扫码注册；首页保持游客态。
    if (errorCode(error) !== 'IDENTITY.DEVICE_REGISTRATION_REQUIRED') {
      console.warn('[miniapp-auth] 静默登录暂未完成', error)
    }
  }
}

async function bootstrapIdentity(sequence: number): Promise<void> {
  // 同一微信如果已经绑定厂家操作员，厂家身份始终先于普通用户/清运身份。
  if (await enterFactoryIfBound(sequence)) return
  await bootstrapOrdinaryIdentity(sequence)
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
    const options = wx.getEnterOptionsSync()
    captureFactoryBindingEntry(options)
    captureOrdinaryDeviceEntry(options)
  },
  onShow() {
    const options = wx.getEnterOptionsSync()
    captureFactoryBindingEntry(options)
    captureOrdinaryDeviceEntry(options)
    // 分包页面负责自身会话和错误展示，避免 App 自动路由造成循环。
    if (isFactoryPage(options.path)) return
    startIdentityBootstrap()
  },
})
