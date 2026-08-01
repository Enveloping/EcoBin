import { requireEntryMode } from '../../utils/guard'
import { isCrossIdentityPreview } from '../../utils/test-entry-preview'

Page({
  data: {
    description: '清运记录接口正在接入，接入后将在这里显示历史作业。',
  },

  onLoad() {
    if (!requireEntryMode(['CLEANING'])) return
    if (isCrossIdentityPreview()) {
      this.setData({
        description:
          '当前为清运端界面预览，登录账号权限未改变，因此不会请求清运记录。',
      })
    }
  },
})
