import { bindCurrentPhone } from '../../api/auth'
import { getSession, markPhoneBound } from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'

Page({
  data: {
    submitting: false,
    alreadyBound: false,
    maskedPhoneNumber: '',
  },

  bindingIntentKey: '' as string,

  async onLoad() {
    this.setData({ alreadyBound: getSession()?.phoneBound ?? false })
    this.bindingIntentKey = await createIdempotencyKey()
  },

  async onGetPhoneNumber(
    event: WechatMiniprogram.CustomEvent<{
      code?: string
      errMsg?: string
    }>,
  ) {
    if (this.data.submitting) return
    const code = event.detail.code
    if (!code) {
      wx.showToast({ title: '需要授权手机号才能完成绑定', icon: 'none' })
      return
    }
    if (!this.bindingIntentKey) {
      this.bindingIntentKey = await createIdempotencyKey()
    }
    this.setData({ submitting: true })
    try {
      const binding = await bindCurrentPhone(
        code,
        this.bindingIntentKey,
      )
      markPhoneBound()
      this.setData({
        alreadyBound: true,
        maskedPhoneNumber: binding.maskedPhoneNumber,
      })
      wx.showToast({ title: '手机号已绑定', icon: 'success' })
    } finally {
      this.setData({ submitting: false })
    }
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/home/home' }) })
  },
})
