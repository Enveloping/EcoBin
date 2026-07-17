interface MallProduct { id: number; name: string; points: number; badge: string; tone: string }

Page({
  data: {
    keyword: '', points: 1280,
    products: [
      { id: 1, name: '有机肥料 5kg', points: 8, badge: '热销', tone: 'mint' },
      { id: 2, name: '环保购物袋', points: 5, badge: '新品', tone: 'blue' },
      { id: 3, name: '竹制餐具套装', points: 15, badge: '', tone: 'sand' },
      { id: 4, name: '节能 LED 灯泡', points: 20, badge: '推荐', tone: 'yellow' },
      { id: 5, name: '可降解垃圾袋', points: 3, badge: '热销', tone: 'rose' },
      { id: 6, name: '净水过滤器', points: 50, badge: '', tone: 'blue' },
    ] as MallProduct[],
  },

  onSearch(e: WechatMiniprogram.CustomEvent<{ value: string }>) { this.setData({ keyword: e.detail.value }) },
  onProduct(e: WechatMiniprogram.TouchEvent) {
    const title = encodeURIComponent(String(e.currentTarget.dataset.title))
    wx.navigateTo({ url: `/pages/placeholder/placeholder?title=${title}` })
  },
  onRedeem(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id)
    const product = this.data.products.find((item) => item.id === id)
    if (!product) return
    wx.showModal({ title: '确认兑换', content: `使用 ${product.points} 积分兑换“${product.name}”？`,
      success: (res) => { if (res.confirm) wx.showToast({ title: '静态演示兑换成功', icon: 'none' }) } })
  },
})
