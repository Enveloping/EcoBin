import {
  listOrganizationAccounts,
  selectOrganizationAccount,
} from '../../api/auth'
import type { OrganizationAccountSummary } from '../../types/api'
import {
  adoptSession,
  getSession,
  routeToEntry,
} from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'

Page({
  data: {
    loading: true,
    errorText: '',
    switchingUid: '',
    staffSession: false,
    accounts: [] as OrganizationAccountSummary[],
  },

  onLoad() {
    const session = getSession()
    if (!session) {
      wx.reLaunch({ url: '/pages/home/home' })
      return
    }
    this.setData({ staffSession: session.audience === 'miniapp-staff' })
    void this.loadAccounts()
  },

  async loadAccounts() {
    const session = getSession()
    if (!session) return
    this.setData({ loading: true, errorText: '' })
    try {
      const result = await listOrganizationAccounts(session.audience)
      this.setData({ accounts: result.accounts })
    } catch (error) {
      this.setData({
        errorText: error instanceof Error && error.message
          ? error.message
          : '机构账号暂时无法读取',
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  onRetry() {
    void this.loadAccounts()
  },

  async onAccountTap(event: WechatMiniprogram.TouchEvent) {
    if (this.data.switchingUid) return
    const session = getSession()
    if (!session) {
      wx.reLaunch({ url: '/pages/home/home' })
      return
    }
    const organizationUserUid = String(
      event.currentTarget.dataset.organizationUserUid || '',
    )
    const selected = event.currentTarget.dataset.selected === true
      || event.currentTarget.dataset.selected === 'true'
    // 管理会话允许选择当前机构，以便退出管理身份进入普通/清运身份。
    if (!organizationUserUid || (selected && session.audience !== 'miniapp-staff')) {
      return
    }

    this.setData({ switchingUid: organizationUserUid })
    try {
      const selectedSession = await selectOrganizationAccount(
        organizationUserUid,
        await createIdempotencyKey(),
        session.audience,
      )
      adoptSession(selectedSession)
      wx.showToast({ title: '已切换机构', icon: 'success' })
      routeToEntry(selectedSession)
    } catch (error) {
      wx.showToast({
        title: error instanceof Error && error.message
          ? error.message
          : '机构切换失败',
        icon: 'none',
      })
    } finally {
      this.setData({ switchingUid: '' })
    }
  },
})
