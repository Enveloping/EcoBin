Page({
  data: { faqs: ['如何使用设备投放垃圾？', '积分如何提现？', '设备无法正常开门怎么办？', '如何预约上门回收？'] },
  onCall() { wx.showModal({ title: '人工客服', content: '服务热线：400-888-8888\n工作时间：9:00-18:00', confirmText: '知道了', showCancel: false }) },
  onOnline() { wx.navigateTo({ url: '/pages/placeholder/placeholder?title=在线客服' }) },
  onFeedback() { wx.navigateTo({ url: '/pages/fault-report/fault-report' }) },
  onFaq(e: WechatMiniprogram.TouchEvent) { wx.showModal({ title: String(e.currentTarget.dataset.question), content: '这是静态演示内容，正式帮助说明将在接口接入后补充。', showCancel: false }) },
})
