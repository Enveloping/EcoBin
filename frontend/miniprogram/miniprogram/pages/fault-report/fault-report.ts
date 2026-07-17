Page({
  data: {
    deviceNo: '', description: '', selectedType: '', imagePath: '',
    faultTypes: ['无法开门', '称重异常', '积分未到账', '屏幕损坏', '其他故障'],
  },

  onInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) { this.setData({ [String(e.currentTarget.dataset.field)]: e.detail.value }) },
  onType(e: WechatMiniprogram.TouchEvent) { this.setData({ selectedType: String(e.currentTarget.dataset.type) }) },
  onChooseImage() {
    wx.chooseMedia({ count: 1, mediaType: ['image'], sourceType: ['album', 'camera'], success: (res) => this.setData({ imagePath: res.tempFiles[0].tempFilePath }) })
  },
  onRemoveImage() { this.setData({ imagePath: '' }) },
  onSubmit() {
    if (!this.data.deviceNo.trim()) return void wx.showToast({ title: '请输入设备编号', icon: 'none' })
    if (!this.data.selectedType) return void wx.showToast({ title: '请选择故障类型', icon: 'none' })
    if (!this.data.description.trim()) return void wx.showToast({ title: '请描述故障情况', icon: 'none' })
    wx.showModal({ title: '提交成功', content: '故障信息已保存为本地演示状态，未上传至服务器。', showCancel: false,
      success: () => this.setData({ deviceNo: '', description: '', selectedType: '', imagePath: '' }) })
  },
})
