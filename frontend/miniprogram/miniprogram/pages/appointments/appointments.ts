interface Appointment {
  id: number; type: string; time: string; address: string; content: string
  status: string; tone: string; cancelable: boolean
}

Page({
  data: {
    appointments: [
      { id: 1, type: '上门回收', time: '2024-07-02 09:00-11:00', address: '湖州市吴兴区红旗路88号', content: '纸箱、金属', status: '待上门', tone: 'waiting', cancelable: true },
      { id: 2, type: '家政保洁', time: '2024-07-05 14:00-16:00', address: '湖州市吴兴区太湖路100号', content: '2小时基础保洁', status: '已确认', tone: 'confirmed', cancelable: false },
      { id: 3, type: '上门回收', time: '2024-06-20 10:00-12:00', address: '湖州市南浔区月河路1号', content: '废旧家电', status: '已完成', tone: 'done', cancelable: false },
    ] as Appointment[],
  },

  onCancel(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id)
    wx.showModal({
      title: '取消预约', content: '确定取消这次上门预约吗？',
      success: (res) => {
        if (!res.confirm) return
        const appointments = this.data.appointments.map((item) => item.id === id ? { ...item, status: '已取消', tone: 'done', cancelable: false } : item)
        this.setData({ appointments })
        wx.showToast({ title: '预约已取消', icon: 'success' })
      },
    })
  },
})
