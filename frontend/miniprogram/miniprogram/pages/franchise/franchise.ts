Page({
  data: { name: '', phone: '', city: '', budget: '' },
  onInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) { this.setData({ [String(e.currentTarget.dataset.field)]: e.detail.value }) },
  onSubmit() {
    if (!this.data.name.trim()) return void wx.showToast({ title: '请输入姓名', icon: 'none' })
    if (!/^1\d{10}$/.test(this.data.phone)) return void wx.showToast({ title: '请输入正确的联系电话', icon: 'none' })
    if (!this.data.city.trim()) return void wx.showToast({ title: '请输入意向城市', icon: 'none' })
    if (!this.data.budget.trim()) return void wx.showToast({ title: '请输入投资预算', icon: 'none' })
    wx.showModal({ title: '登记成功', content: '意向信息已保存为本地演示状态，我们将在正式服务上线后与您联系。', showCancel: false,
      success: () => this.setData({ name: '', phone: '', city: '', budget: '' }) })
  },
})
