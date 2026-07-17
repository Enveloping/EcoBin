interface DemoOrder {
  id: number
  type: string
  category: string
  date: string
  title: string
  orderNo: string
  status: string
  amount: string
  expense?: boolean
}

const ORDERS: DemoOrder[] = [
  { id: 1, type: '智能回收', category: '回收', date: '2024-06-30', title: '纸张 0.5kg', orderNo: 'RC20240630001', status: '已结算', amount: '+¥1.20' },
  { id: 2, type: '上门回收', category: '回收', date: '2024-06-29', title: '纸箱 3kg、金属 1kg', orderNo: 'RC20240629003', status: '已完成', amount: '+¥8.50' },
  { id: 3, type: '智能回收', category: '回收', date: '2024-06-28', title: '塑料瓶 0.3kg', orderNo: 'RC20240628002', status: '已结算', amount: '+¥0.60' },
  { id: 4, type: '家政服务', category: '家政', date: '2024-06-20', title: '上门保洁 2h', orderNo: 'HK20240620001', status: '已完成', amount: '-¥88.00', expense: true },
  { id: 5, type: '账户提现', category: '提现', date: '2024-06-18', title: '提现至微信零钱', orderNo: 'WD20240618001', status: '审核中', amount: '-¥20.00', expense: true },
]

Page({
  data: {
    tabs: ['全部', '回收', '家政', '提现'],
    active: '全部',
    visibleOrders: ORDERS,
  },

  onTab(e: WechatMiniprogram.TouchEvent) {
    const active = String(e.currentTarget.dataset.tab)
    this.setData({ active, visibleOrders: active === '全部' ? ORDERS : ORDERS.filter((item) => item.category === active) })
  },
})
