import { ensureLoggedIn, routeToEntry } from '../../utils/auth'
import {
  captureOrdinaryDeviceEntryFromQuery,
  clearPendingDeviceEntry,
  peekPendingDeviceEntry,
  routePendingDeviceEntry,
} from '../../utils/device-entry-intent'
import { MiniappApiProblem } from '../../utils/request'
import { registrationDeploymentCode } from '../../utils/registration-source'

Page({
  data: {
    loading: true,
    error: '',
    diagnostic: '',
  },

  registrationDeploymentCode: undefined as string | undefined,
  loadedAt: 0,
  loginAttempt: 0,

  onLoad(options: Record<string, string | undefined>) {
    const captured = captureOrdinaryDeviceEntryFromQuery(options)
    const currentSource = registrationDeploymentCode(options)
    const previousPending = peekPendingDeviceEntry()
    if (
      currentSource
      && previousPending
      && previousPending.deploymentCode !== currentSource
      && !previousPending.idempotencyKey
      && !previousPending.accepted
    ) {
      clearPendingDeviceEntry()
    }
    this.registrationDeploymentCode =
      currentSource
      ?? captured?.deploymentCode
      ?? peekPendingDeviceEntry()?.deploymentCode
    this.loadedAt = Date.now()
    void this.doLogin()
  },

  onShow() {
    const pending = peekPendingDeviceEntry()
    if (
      pending
      && pending.capturedAt > this.loadedAt
      && pending.deploymentCode !== this.registrationDeploymentCode
    ) {
      this.registrationDeploymentCode = pending.deploymentCode
      void this.doLogin()
    }
  },

  async doLogin() {
    const attempt = ++this.loginAttempt
    const deploymentCode = this.registrationDeploymentCode
    this.setData({ loading: true, error: '', diagnostic: '' })
    try {
      const session = await ensureLoggedIn(
        deploymentCode
          ? { deploymentCode }
          : undefined,
      )
      if (attempt !== this.loginAttempt) return
      if (!routePendingDeviceEntry(session)) routeToEntry(session)
    } catch (error) {
      if (attempt !== this.loginAttempt) return
      const failure = loginFailure(error)
      console.error('[miniapp-login] 登录失败', failure.log)
      this.setData({
        loading: false,
        error: failure.message,
        diagnostic: failure.diagnostic,
      })
    }
  },

  onRetry() {
    void this.doLogin()
  },
})

interface LoginFailure {
  message: string
  diagnostic: string
  log: Record<string, string | number>
}

function loginFailure(error: unknown): LoginFailure {
  if (error instanceof MiniappApiProblem) {
    const requestId = error.requestId.trim()
    return {
      message: error.message || '登录失败，请重试',
      diagnostic: requestId
        ? `错误码：${error.code} · 请求编号：${requestId}`
        : `错误码：${error.code}`,
      log: {
        status: error.status,
        code: error.code,
        message: error.message,
        requestId: requestId || '未返回',
      },
    }
  }

  const message = error instanceof Error && error.message.trim()
    ? error.message
    : '登录失败，请重试'
  return {
    message,
    diagnostic: '错误码：WECHAT.CLIENT_LOGIN_FAILED',
    log: {
      code: 'WECHAT.CLIENT_LOGIN_FAILED',
      message,
    },
  }
}
