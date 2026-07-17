import { startDoorEntry } from '../../utils/door-entry'

Page({
  data: {
    devices: [
      { name: '红旗路回收站', address: '湖州市吴兴区红旗路88号', distance: '128m', status: '空闲', tone: 'free', categories: ['纸张', '金属', '塑料'] },
      { name: '月河街道社区站', address: '湖州市南浔区月河路1号', distance: '356m', status: '使用中', tone: 'busy', categories: ['纸张', '玻璃'] },
      { name: '爱山街道回收点', address: '湖州市吴兴区爱山路320号', distance: '580m', status: '空闲', tone: 'free', categories: ['纸张', '金属', '塑料', '玻璃'] },
      { name: '太湖科技园站', address: '湖州市吴兴区太湖路100号', distance: '1.2km', status: '维护中', tone: 'repair', categories: ['纸张'] },
    ],
  },

  onRelocate() {
    wx.showToast({ title: '已更新演示位置', icon: 'success' })
  },

  onMap() {
    wx.showToast({ title: '静态地图仅作展示', icon: 'none' })
  },

  onOpen() {
    startDoorEntry()
  },
})
