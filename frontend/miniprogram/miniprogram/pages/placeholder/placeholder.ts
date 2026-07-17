Page({
  data: { title: '该功能' },
  onLoad(options: Record<string, string | undefined>) {
    const title = decodeURIComponent(options.title || '该功能')
    this.setData({ title })
    wx.setNavigationBarTitle({ title })
  },
  onBack() { wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/home/home' }) }) },
})
