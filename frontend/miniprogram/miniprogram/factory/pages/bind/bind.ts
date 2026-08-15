import {
  FactoryApiProblem,
  loginFactory,
} from '../../../api/factory'
import {
  consumeFactoryBindingToken,
  preferFactoryMode,
  routeToFactoryAcceptance,
} from '../../../utils/factory-mode'

function errorText(error: unknown): string {
  if (error instanceof FactoryApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ${error.requestId}）`
      : error.message
  }
  return error instanceof Error ? error.message : '绑定失败，请稍后重试'
}

Page({
  data: {
    state: 'BINDING' as 'BINDING' | 'SUCCEEDED' | 'FAILED',
    displayName: '',
    operatorCode: '',
    errorMessage: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    const token = consumeFactoryBindingToken(options.scene)
    if (!token) {
      this.setData({
        state: 'FAILED',
        errorMessage: '绑定码无效或已过期，请让平台管理员重新生成。',
      })
      return
    }
    void this.bind(token)
  },

  async bind(token: string) {
    try {
      const session = await loginFactory(token)
      preferFactoryMode()
      this.setData({
        state: 'SUCCEEDED',
        displayName: session.displayName,
        operatorCode: session.operatorCode,
      })
      wx.showToast({ title: '厂家身份绑定成功', icon: 'success' })
      routeToFactoryAcceptance()
    } catch (error) {
      this.setData({
        state: 'FAILED',
        errorMessage: errorText(error),
      })
    }
  },
})
