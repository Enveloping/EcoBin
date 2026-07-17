let countdownTimer: number | undefined

Page({
  data: { phone: '', code: '', seconds: 0 },

  onUnload() { if (countdownTimer) clearInterval(countdownTimer) },
  onInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) { this.setData({ [String(e.currentTarget.dataset.field)]: e.detail.value }) },

  onSendCode() {
    if (this.data.seconds > 0) return
    if (!/^1\d{10}$/.test(this.data.phone)) return void wx.showToast({ title: '请输入正确的手机号', icon: 'none' })
    this.setData({ seconds: 60 })
    wx.showToast({ title: '演示验证码已发送', icon: 'none' })
    countdownTimer = setInterval(() => {
      const seconds = this.data.seconds - 1
      this.setData({ seconds })
      if (seconds <= 0 && countdownTimer) { clearInterval(countdownTimer); countdownTimer = undefined }
    }, 1000) as unknown as number
  },

  onSubmit() {
    if (!/^1\d{10}$/.test(this.data.phone)) return void wx.showToast({ title: '请输入正确的手机号', icon: 'none' })
    if (!/^\d{4,6}$/.test(this.data.code)) return void wx.showToast({ title: '请输入验证码', icon: 'none' })
    wx.showModal({ title: '绑定成功', content: `已在本地演示中绑定 ${this.data.phone.replace(/(\d{3})\d{4}(\d{4})/, '$1****$2')}`, showCancel: false })
  },
})
