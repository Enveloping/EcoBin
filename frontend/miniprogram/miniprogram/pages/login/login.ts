import { ensureLoggedIn, routeToEntry } from '../../utils/auth'

Page({
  data: {
    loading: true,
    error: '',
  },

  registrationDeploymentCode: undefined as string | undefined,

  onLoad(options: Record<string, string | undefined>) {
    this.registrationDeploymentCode = registrationDeploymentCode(options)
    this.doLogin()
  },

  async doLogin() {
    this.setData({ loading: true, error: '' })
    try {
      const session = await ensureLoggedIn(
        this.registrationDeploymentCode
          ? { deploymentCode: this.registrationDeploymentCode }
          : undefined,
      )
      routeToEntry(session)
    } catch (e) {
      this.setData({ loading: false, error: '登录失败，请重试' })
    }
  },

  onRetry() {
    this.doLogin()
  },
})

function registrationDeploymentCode(
  options: Record<string, string | undefined>,
): string | undefined {
  const direct = options.deploymentCode?.trim()
  if (direct) return direct.slice(0, 64)
  if (!options.scene) return undefined
  const scene = decodeURIComponent(options.scene).trim()
  const matched = /(?:^|&)deploymentCode=([^&]+)/.exec(scene)
  const value = (matched?.[1] ?? scene).trim()
  return value ? value.slice(0, 64) : undefined
}
