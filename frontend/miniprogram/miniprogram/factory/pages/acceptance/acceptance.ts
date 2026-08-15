import {
  correctFactoryBag,
  ensureFactorySession,
  factoryAcceptance,
  FactoryApiProblem,
  installFactoryBag,
  logoutFactory,
  verifyFactoryBag,
  type FactoryAcceptance,
  type FactorySession,
} from '../../../api/factory'
import { ensureLoggedIn, routeToEntry } from '../../../utils/auth'
import { createIdempotencyKey } from '../../../utils/command-intent'
import { preferOrdinaryMode } from '../../../utils/factory-mode'
import { formatLocalDateTime } from '../../../utils/local-time'
import { parseDeviceLinkUrl } from '../../../utils/ordinary-device-link'

const DEVICE_CODE = /^Dv_[A-Za-z0-9_-]{24,61}$/

interface SlotView {
  portNo: number
  installed: boolean
  bagCode: string
  installedAtText: string
  verificationStatus: 'EMPTY' | 'NEEDS_FACTORY_SCAN' | 'FACTORY_VERIFIED' | 'LEGACY_GRANDFATHERED'
  statusText: string
  actionText: string
  actionDisabled: boolean
}

function slotPresentation(status: SlotView['verificationStatus']) {
  switch (status) {
    case 'NEEDS_FACTORY_SCAN':
      return { statusText: '待实体补扫', actionText: '扫描核验', actionDisabled: false }
    case 'FACTORY_VERIFIED':
      return { statusText: '厂家已核验', actionText: '更正袋码', actionDisabled: false }
    case 'LEGACY_GRANDFATHERED':
      return { statusText: '历史设备豁免', actionText: '无需补扫', actionDisabled: true }
    default:
      return { statusText: '尚未登记', actionText: '扫描袋码', actionDisabled: false }
  }
}

function errorCode(error: unknown): string {
  if (!error || typeof error !== 'object') return ''
  const code = (error as { code?: unknown }).code
  return typeof code === 'string' ? code : ''
}

function errorText(error: unknown): string {
  if (error instanceof FactoryApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ${error.requestId}）`
      : error.message
  }
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

function deviceCodeFromScan(raw: string): string | undefined {
  const value = raw.trim()
  if (DEVICE_CODE.test(value)) return value
  return parseDeviceLinkUrl(value)?.deviceCode
}

Page({
  data: {
    booting: true,
    unauthorized: false,
    switchingIdentity: false,
    loading: false,
    submittingPort: 0,
    operatorCode: '',
    displayName: '',
    deviceCode: '',
    hardwareSn: '',
    expectedPortCount: 0,
    acceptanceStatus: '',
    complete: false,
    acceptanceCanStart: false,
    slots: [] as SlotView[],
    errorMessage: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    void this.bootstrap(options.deviceCode)
  },

  async bootstrap(deviceCode?: string) {
    this.setData({ booting: true, unauthorized: false, errorMessage: '' })
    try {
      const session = await ensureFactorySession()
      this.adoptSession(session)
      if (deviceCode && DEVICE_CODE.test(deviceCode)) {
        await this.loadDevice(deviceCode)
      }
    } catch (error) {
      const unauthorized = errorCode(error) === 'IDENTITY.FACTORY_BINDING_REQUIRED'
        || (error instanceof FactoryApiProblem && error.status === 401)
      this.setData({
        unauthorized,
        errorMessage: unauthorized ? '' : errorText(error),
      })
    } finally {
      this.setData({ booting: false })
    }
  },

  adoptSession(session: FactorySession) {
    this.setData({
      unauthorized: false,
      operatorCode: session.operatorCode,
      displayName: session.displayName,
      errorMessage: '',
    })
    if (session.newlyBound) {
      wx.showToast({ title: '厂家身份绑定成功', icon: 'success' })
    }
  },

  async switchIdentity() {
    if (this.data.switchingIdentity) return
    this.setData({ switchingIdentity: true })
    try {
      // 先确认同一微信确有普通用户或清运身份；失败时仍留在厂家端。
      const ordinary = await ensureLoggedIn()
      preferOrdinaryMode()
      try {
        await logoutFactory()
      } catch (logoutError) {
        console.warn('[factory-auth] 厂家会话服务端注销暂未确认', logoutError)
      }
      routeToEntry(ordinary)
    } catch (error) {
      if (errorCode(error) === 'IDENTITY.DEVICE_REGISTRATION_REQUIRED') {
        await wx.showModal({
          title: '暂无其他身份',
          content: '当前微信只有厂家操作员身份。普通用户首次使用需扫描已验收设备码；清运人员需先由所属机构创建账号。',
          showCancel: false,
          confirmText: '我知道了',
        })
      } else {
        wx.showToast({ title: errorText(error), icon: 'none' })
      }
    } finally {
      this.setData({ switchingIdentity: false })
    }
  },

  async scanDevice() {
    try {
      const scan = await wx.scanCode({ scanType: ['qrCode', 'barCode'] })
      const deviceCode = deviceCodeFromScan(String(scan.result || ''))
      if (!deviceCode) {
        wx.showToast({ title: '未识别到有效设备二维码', icon: 'none' })
        return
      }
      await this.loadDevice(deviceCode)
    } catch (error) {
      if (errorText(error).includes('cancel')) return
      this.setData({ errorMessage: errorText(error) })
    }
  },

  async loadDevice(deviceCode: string) {
    this.setData({ loading: true, errorMessage: '' })
    try {
      this.renderAcceptance(await factoryAcceptance(deviceCode))
    } catch (error) {
      this.setData({
        unauthorized: error instanceof FactoryApiProblem
          && error.status === 401,
        errorMessage: errorText(error),
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  renderAcceptance(view: FactoryAcceptance) {
    const byPort = new Map(view.factoryBags.map((bag) => [bag.portNo, bag]))
    const slots: SlotView[] = []
    for (let portNo = 1; portNo <= view.expectedPortCount; portNo += 1) {
      const bag = byPort.get(portNo)
      const verificationStatus = bag?.verificationStatus || 'EMPTY'
      slots.push({
        portNo,
        installed: !!bag,
        bagCode: bag?.bagCode || '',
        installedAtText: bag ? formatLocalDateTime(bag.installedAt) : '',
        verificationStatus,
        ...slotPresentation(verificationStatus),
      })
    }
    this.setData({
      deviceCode: view.deviceCode,
      hardwareSn: view.hardwareSn,
      expectedPortCount: view.expectedPortCount,
      acceptanceStatus: view.acceptanceStatus,
      complete: view.allFactoryBagsVerified,
      acceptanceCanStart: view.acceptanceCanStart,
      slots,
      errorMessage: '',
    })
  },

  async scanBag(event: WechatMiniprogram.TouchEvent) {
    if (this.data.acceptanceStatus === 'PASSED') return
    const portNo = Number(event.currentTarget.dataset.portNo)
    if (!Number.isInteger(portNo) || this.data.submittingPort) return
    const slot = this.data.slots.find((item) => item.portNo === portNo)
    if (!slot || slot.actionDisabled) return
    try {
      const scan = await wx.scanCode({ scanType: ['qrCode', 'barCode'] })
      const bagCode = String(scan.result || '').trim()
      if (!bagCode.startsWith('EB1_')) {
        wx.showToast({ title: '请扫描平台签发的 EB1 袋码', icon: 'none' })
        return
      }
      if (!slot.installed) {
        await this.install(portNo, bagCode)
      } else if (
        slot.verificationStatus === 'NEEDS_FACTORY_SCAN'
        && slot.bagCode === bagCode
      ) {
        await this.verify(portNo, bagCode)
      } else {
        await this.correct(portNo, bagCode)
      }
    } catch (error) {
      if (errorText(error).includes('cancel')) return
      wx.showToast({ title: errorText(error), icon: 'none' })
    }
  },

  async install(portNo: number, bagCode: string) {
    const confirmation = await wx.showModal({
      title: `登记 ${portNo} 号投口`,
      content: '请再次核对实体袋确实安装在当前投口。登记后如发现扫错，必须使用“更正袋码”留下原因。',
      confirmText: '确认登记',
    })
    if (!confirmation.confirm) return
    this.setData({ submittingPort: portNo })
    try {
      this.renderAcceptance(await installFactoryBag(
        this.data.deviceCode,
        portNo,
        bagCode,
        await createIdempotencyKey(),
      ))
      wx.showToast({ title: `${portNo} 号投口已登记`, icon: 'success' })
    } finally {
      this.setData({ submittingPort: 0 })
    }
  },

  async correct(portNo: number, bagCode: string) {
    const reason = await wx.showModal({
      title: `更正 ${portNo} 号投口`,
      content: '请输入为什么需要更换已登记的袋码。如存在原袋码占用会同时释放，更正历史会永久保留。',
      editable: true,
      placeholderText: '例如：首次扫码时接线人员拿错了袋子',
      confirmText: '确认更正',
    })
    const reasonText = String(reason.content || '').trim()
    if (!reason.confirm) return
    if (!reasonText) {
      wx.showToast({ title: '必须填写更正原因', icon: 'none' })
      return
    }
    this.setData({ submittingPort: portNo })
    try {
      this.renderAcceptance(await correctFactoryBag(
        this.data.deviceCode,
        portNo,
        bagCode,
        reasonText,
        await createIdempotencyKey(),
      ))
      wx.showToast({ title: `${portNo} 号投口已更正`, icon: 'success' })
    } finally {
      this.setData({ submittingPort: 0 })
    }
  },

  async verify(portNo: number, bagCode: string) {
    const confirmation = await wx.showModal({
      title: `核验 ${portNo} 号投口`,
      content: '扫码结果与平台已有记录一致。确认实体袋确实安装在当前投口后，该投口才会计入出厂验收条件。',
      confirmText: '确认核验',
    })
    if (!confirmation.confirm) return
    this.setData({ submittingPort: portNo })
    try {
      this.renderAcceptance(await verifyFactoryBag(
        this.data.deviceCode,
        portNo,
        bagCode,
        await createIdempotencyKey(),
      ))
      wx.showToast({ title: `${portNo} 号投口已核验`, icon: 'success' })
    } finally {
      this.setData({ submittingPort: 0 })
    }
  },

  refresh() {
    if (this.data.deviceCode) void this.loadDevice(this.data.deviceCode)
  },
})
