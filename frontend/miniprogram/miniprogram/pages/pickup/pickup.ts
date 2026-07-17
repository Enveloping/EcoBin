interface PickupCategory { name: string; selected: boolean }

Page({
  data: {
    step: 1,
    steps: [1, 2, 3],
    categories: ['纸箱纸板', '旧报纸', '金属类', '废旧家电', '玻璃瓶', '塑料瓶', '衣物', '旧书籍'].map((name) => ({ name, selected: false })) as PickupCategory[],
    categoryText: '', phone: '', address: '', time: '', note: '',
  },

  onCategory(e: WechatMiniprogram.TouchEvent) {
    const index = Number(e.currentTarget.dataset.index)
    const categories = this.data.categories.map((item, current) => current === index ? { ...item, selected: !item.selected } : item)
    this.setData({ categories })
  },

  onInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ [String(e.currentTarget.dataset.field)]: e.detail.value })
  },

  onTime(e: WechatMiniprogram.PickerChange) { this.setData({ time: String(e.detail.value) }) },

  onNext() {
    if (this.data.step === 1) {
      const categoryText = this.data.categories.filter((item) => item.selected).map((item) => item.name).join('、')
      if (!categoryText) return void wx.showToast({ title: '请至少选择一个品类', icon: 'none' })
      this.setData({ step: 2, categoryText }); return
    }
    if (!/^1\d{10}$/.test(this.data.phone)) return void wx.showToast({ title: '请输入正确的联系电话', icon: 'none' })
    if (!this.data.address.trim()) return void wx.showToast({ title: '请输入回收地址', icon: 'none' })
    if (!this.data.time) return void wx.showToast({ title: '请选择预约时间', icon: 'none' })
    this.setData({ step: 3 })
  },

  onPrevious() { if (this.data.step > 1) this.setData({ step: this.data.step - 1 }) },

  onConfirm() {
    wx.showModal({ title: '预约成功', content: '静态演示预约已生成，可在预约记录中查看示例数据。', showCancel: false,
      success: () => wx.redirectTo({ url: '/pages/appointments/appointments' }) })
  },
})
