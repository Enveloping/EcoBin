import { bindCurrentPhone } from '../../api/auth'
import {
  cleanOperation,
  cleanOptions,
  startCleanOperation,
} from '../../api/clean'
import { FEATURES } from '../../config/index'
import type {
  CleanOperationStatus,
  CleanOptionsView,
  CleanPortOption,
} from '../../types/api'
import { getEntryMode, getSession, markPhoneBound } from '../../utils/auth'
import {
  acceptCleanOperationIntent,
  forgetCleanOperationIntent,
  newPendingCleanOperationIntent,
  parseCleaningDeviceCode,
  parseRawBagQr,
  projectCleanOperationIntent,
  rememberCleanOperationIntent,
  restoreCleanOperationIntent,
  sameCleanOperationRequest,
  type PendingCleanOperationIntent,
} from '../../utils/clean-operation-intent'
import {
  autoSelectedCleanPortNo,
  cleanBlockerText,
  cleanOperationDisposition,
} from '../../utils/cleaning-flow'
import { createIdempotencyKey } from '../../utils/command-intent'
import { requireEntryMode } from '../../utils/guard'
import {
  isWechatPhoneGrantCancelled,
  reportPhoneBindingError,
  reportWechatPhoneGrantError,
  type WechatPhoneGrantDetail,
} from '../../utils/phone-grant'
import { MiniappApiProblem } from '../../utils/request'

interface CleanPortView extends CleanPortOption {
  title: string
  currentBagText: string
  fullnessText: string
  blockerText: string
}

const FULLNESS_TEXT: Record<string, string> = {
  UNKNOWN: '满溢状态未知',
  CHECKING: '正在检测满溢状态',
  NOT_FULL: '未满',
  SUSPECTED_FULL: '疑似满溢',
  FULL: '已满溢',
  SOURCE_FAILED: '满溢检测失败',
}

function decodeDeviceCode(value?: string): string {
  if (!value) return ''
  try {
    return parseCleaningDeviceCode(decodeURIComponent(value))
  } catch {
    return ''
  }
}

function portView(port: CleanPortOption): CleanPortView {
  const fullness = FULLNESS_TEXT[port.fullnessStatus] ?? '满溢状态未知'
  return {
    ...port,
    title: port.displayName || `${port.portNo} 号投口`,
    currentBagText: port.currentBagQr || '当前未绑定回收袋',
    fullnessText: port.fullnessPercent
      ? `${fullness} · ${port.fullnessPercent}%`
      : fullness,
    blockerText: port.blockers
      .map(cleanBlockerText)
      .join('；'),
  }
}

function knownClientFailure(error: unknown): boolean {
  return error instanceof MiniappApiProblem
    && error.status >= 400
    && error.status < 500
}

function statusCopy(status: CleanOperationStatus): {
  title: string
  description: string
} {
  switch (status) {
    case 'PREPARED':
      return {
        title: '作业已受理',
        description: '后台已保存清运操作，正在等待设备接收。请留在设备旁。',
      }
    case 'EDGE_SAVED':
      return {
        title: '设备已准备',
        description: '设备已可靠保存作业，请按设备屏幕提示完成换袋。',
      }
    case 'IN_PROGRESS':
      return {
        title: '现场清运中',
        description: '请在设备端完成换袋、手动关门，并在屏幕上确认完成。',
      }
    case 'RECOVERY_REQUIRED':
      return {
        title: '需要现场恢复',
        description: '操作已停止自动推进，原操作信息仍被保留。请联系管理员处理。',
      }
    case 'PRE_UNLOCK_ENDED':
      return {
        title: '本次清运未开始',
        description: '设备已确认首次解锁前结束，本次没有形成清运记录。',
      }
    case 'COMPLETED':
      return {
        title: '清运完成',
        description: '换袋结果已保存。清运记录可在“我的 → 清运记录”查看。',
      }
    case 'ABORTED':
      return {
        title: '本次清运已安全中止',
        description: '设备在执行期间重启，系统已中止原操作并进入安全联锁。请联系管理员现场确认。',
      }
  }
}

Page({
  intent: null as PendingCleanOperationIntent | null,
  pollTimer: undefined as ReturnType<typeof setTimeout> | undefined,
  visible: false,
  polling: false,
  submitting: false,
  phoneBindingIntentKey: '',
  pendingBagScanAfterPhone: false,

  data: {
    deviceCode: '',
    previewOnly: false,
    serviceUnavailable: !FEATURES.targetCleaningDataApi,
    stage: 'loading',
    loading: true,
    errorMessage: '',
    deviceName: '',
    address: '',
    deviceBusy: false,
    recoverableCount: 0,
    ports: [] as CleanPortView[],
    selectedPortNo: 0,
    selectedPortTitle: '',
    removedBagQr: '',
    installedBagQr: '',
    operationUid: '',
    statusTitle: '',
    statusDescription: '',
    cleanRecordNo: '',
    showPhoneGrant: false,
    phoneGrantSubmitting: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    if (!requireEntryMode(['CLEANING'])) return
    const deviceCode = decodeDeviceCode(options.deviceCode)
    const previewOnly = getEntryMode() !== 'CLEANING'
    this.setData({ deviceCode, previewOnly })
    if (previewOnly) {
      this.setData({
        loading: false,
        stage: 'preview',
        errorMessage: '当前仅预览清运端，登录账号权限未改变，因此不会请求清运接口。',
      })
      return
    }
    if (!FEATURES.targetCleaningDataApi) {
      this.setData({ loading: false, stage: 'unavailable' })
      return
    }
    if (!deviceCode) {
      this.setData({
        loading: false,
        stage: 'error',
        errorMessage: '设备公开码无效，请返回后重新扫描设备二维码。',
      })
      return
    }

    const restored = restoreCleanOperationIntent()
    if (restored && restored.deviceCode !== deviceCode) {
      this.intent = restored
      this.setData({
        loading: false,
        stage: 'blocked',
        operationUid: restored.operationUid || '',
        errorMessage:
          `设备 ${restored.deviceCode} 还有一笔结果未确定的清运操作，`
          + '为避免重复开门，当前不能发起另一笔操作。',
      })
      return
    }
    if (restored) {
      this.restoreIntent(restored)
      return
    }
    void this.loadOptions()
  },

  onShow() {
    this.visible = true
    if (this.intent?.operationUid && this.shouldPoll()) {
      this.schedulePoll(0)
    }
  },

  onHide() {
    this.visible = false
    this.clearPollTimer()
  },

  onUnload() {
    this.visible = false
    this.clearPollTimer()
  },

  restoreIntent(intent: PendingCleanOperationIntent) {
    this.intent = intent
    this.setData({
      loading: false,
      selectedPortNo: intent.portNo,
      selectedPortTitle: `${intent.portNo} 号投口`,
      installedBagQr: intent.installedBagQr,
      operationUid: intent.operationUid || '',
      cleanRecordNo: intent.cleanRecordNo || '',
    })
    if (!intent.operationUid) {
      this.setData({
        stage: 'submit-uncertain',
        statusTitle: '受理结果尚未确定',
        statusDescription: '本地已保留原请求标识。点击继续时会复用原标识，不会创建新的开门意图。',
      })
      return
    }
    if (intent.lastStatus === 'RECOVERY_REQUIRED') {
      this.applyStatus('RECOVERY_REQUIRED')
      return
    }
    if (intent.lastStatus === 'COMPLETED') {
      this.applyStatus('COMPLETED')
      return
    }
    if (intent.lastStatus === 'PRE_UNLOCK_ENDED') {
      this.applyStatus('PRE_UNLOCK_ENDED')
      return
    }
    if (intent.lastStatus === 'ABORTED') {
      this.applyStatus('ABORTED')
      return
    }
    this.applyStatus(intent.lastStatus ?? 'PREPARED')
    if (this.visible) this.schedulePoll(0)
  },

  async loadOptions() {
    if (!this.data.deviceCode || this.data.previewOnly) return
    this.setData({ loading: true, stage: 'loading', errorMessage: '' })
    try {
      const options = await cleanOptions(this.data.deviceCode, false)
      this.applyOptions(options)
    } catch (error) {
      this.setData({
        loading: false,
        stage: 'error',
        errorMessage: error instanceof MiniappApiProblem && error.status === 404
          ? '未找到该设备，或当前账号无权在此设备清运。'
          : '清运选项暂时无法加载，请检查网络后重试。',
      })
    }
  },

  applyOptions(options: CleanOptionsView) {
    const ports = options.ports.map(portView)
    const autoSelectedPortNo = autoSelectedCleanPortNo(ports)
    const autoSelected = ports.find(
      (port) => port.portNo === autoSelectedPortNo,
    )
    this.setData({
      loading: false,
      stage: 'options',
      deviceName: options.displayName || '未命名回收箱',
      address: options.address || '暂无设备地址',
      deviceBusy: options.deviceBusy,
      recoverableCount: options.recoverableOperations.length,
      ports,
      selectedPortNo: autoSelected?.portNo ?? 0,
      selectedPortTitle: autoSelected?.title ?? '',
      removedBagQr: autoSelected?.currentBagQr ?? '',
      errorMessage: '',
    })
  },

  onRetryOptions() {
    void this.loadOptions()
  },

  onSelectPort(event: WechatMiniprogram.TouchEvent) {
    if (this.data.stage !== 'options') return
    const portNo = Number(event.currentTarget.dataset.portNo)
    const port = this.data.ports.find((item) => item.portNo === portNo)
    if (!port || !port.cleaningAllowed) return
    this.setData({
      selectedPortNo: port.portNo,
      selectedPortTitle: port.title,
      removedBagQr: port.currentBagQr || '',
      installedBagQr: '',
      errorMessage: '',
    })
  },

  onScanBag() {
    if (!this.data.selectedPortNo || this.data.previewOnly) {
      if (!this.data.selectedPortNo) {
        wx.showToast({ title: '请先选择可清运投口', icon: 'none' })
      }
      return
    }
    const session = getSession()
    if (!session?.phoneBound) {
      this.pendingBagScanAfterPhone = true
      this.setData({ showPhoneGrant: true })
      return
    }
    this.scanBagQr()
  },

  scanBagQr() {
    wx.scanCode({
      scanType: ['qrCode'],
      success: ({ result }) => {
        const bagQr = parseRawBagQr(result)
        if (!bagQr) {
          wx.showToast({
            title: '换入袋二维码必须是原始袋码',
            icon: 'none',
          })
          return
        }
        this.setData({
          installedBagQr: bagQr,
          stage: 'confirm',
          errorMessage: '',
        })
      },
      fail: ({ errMsg }) => {
        if (!/cancel/i.test(errMsg)) {
          wx.showToast({ title: '袋码扫描失败，请重试', icon: 'none' })
        }
      },
    })
  },

  async ensurePhoneBindingIntent(): Promise<string> {
    if (!this.phoneBindingIntentKey) {
      this.phoneBindingIntentKey = await createIdempotencyKey()
    }
    return this.phoneBindingIntentKey
  },

  onPhoneSheetMaskTap() {
    this.cancelPhoneGrant()
  },

  onPhoneSheetPanelTap() {
    // 阻止点击面板冒泡关闭。
  },

  onPhoneGrantCancel() {
    this.cancelPhoneGrant()
  },

  cancelPhoneGrant() {
    if (this.data.phoneGrantSubmitting) return
    this.pendingBagScanAfterPhone = false
    this.setData({ showPhoneGrant: false })
  },

  async onGetPhoneNumber(
    event: WechatMiniprogram.CustomEvent<WechatPhoneGrantDetail>,
  ) {
    if (this.data.phoneGrantSubmitting) return
    if (isWechatPhoneGrantCancelled(event.detail)) {
      this.cancelPhoneGrant()
      return
    }
    const code = event.detail.code?.trim()
    if (!code) {
      reportWechatPhoneGrantError(event.detail)
      return
    }
    this.setData({ phoneGrantSubmitting: true })
    try {
      const key = await this.ensurePhoneBindingIntent()
      await bindCurrentPhone(code, key)
      markPhoneBound()
      this.phoneBindingIntentKey = ''
      const continueScan = this.pendingBagScanAfterPhone
      this.pendingBagScanAfterPhone = false
      this.setData({ showPhoneGrant: false })
      if (continueScan) this.scanBagQr()
    } catch (error) {
      reportPhoneBindingError(error)
    } finally {
      this.setData({ phoneGrantSubmitting: false })
    }
  },

  onRescanBag() {
    this.setData({ stage: 'options', installedBagQr: '' }, () => {
      this.onScanBag()
    })
  },

  async onConfirmStart() {
    if (this.submitting) return
    const { deviceCode, selectedPortNo, installedBagQr } = this.data
    if (!selectedPortNo || !parseRawBagQr(installedBagQr)) return

    let intent = restoreCleanOperationIntent()
    if (intent && !sameCleanOperationRequest(
      intent,
      deviceCode,
      selectedPortNo,
      installedBagQr,
    )) {
      this.setData({
        stage: 'blocked',
        errorMessage: '已有一笔结果未确定的清运操作，不能生成新的开门请求。',
      })
      return
    }
    if (!intent) {
      const idempotencyKey = await createIdempotencyKey()
      intent = newPendingCleanOperationIntent(
        deviceCode,
        selectedPortNo,
        installedBagQr,
        idempotencyKey,
      )
      // 必须先持久化完整请求事实和幂等键，再发起可能开门的网络请求。
      rememberCleanOperationIntent(intent)
    }
    this.intent = intent
    await this.submitIntent()
  },

  async submitIntent() {
    const intent = this.intent ?? restoreCleanOperationIntent()
    if (!intent || intent.operationUid || this.submitting) return
    this.intent = intent
    this.submitting = true
    this.setData({
      stage: 'submitting',
      statusTitle: '正在提交清运请求',
      statusDescription: '本地已保存请求标识，请勿重复扫描或离开设备。',
      errorMessage: '',
    })
    try {
      const accepted = await startCleanOperation(
        intent.deviceCode,
        intent.portNo,
        intent.installedBagQr,
        intent.idempotencyKey,
      )
      this.intent = acceptCleanOperationIntent(intent, accepted)
      this.setData({ operationUid: accepted.operationUid })
      this.applyStatus(accepted.status)
      this.schedulePoll(accepted.recommendedPollAfterMs)
    } catch (error) {
      if (knownClientFailure(error)) {
        forgetCleanOperationIntent()
        this.intent = null
        this.setData({
          stage: 'confirm',
          errorMessage: error instanceof Error
            ? error.message
            : '当前条件不允许开始清运，请重新检查。',
        })
      } else {
        this.setData({
          stage: 'submit-uncertain',
          statusTitle: '受理结果尚未确定',
          statusDescription: '网络结果不确定。本地已保留原请求标识，继续查询或重试不会生成第二次开门意图。',
          errorMessage: '',
        })
      }
    } finally {
      this.submitting = false
    }
  },

  onContinueOperation() {
    const intent = this.intent ?? restoreCleanOperationIntent()
    if (!intent) return
    this.intent = intent
    if (intent.operationUid) {
      this.setData({ stage: 'polling', errorMessage: '' })
      this.schedulePoll(0)
    } else {
      void this.submitIntent()
    }
  },

  shouldPoll(): boolean {
    const status = this.intent?.lastStatus
    return status === null
      || cleanOperationDisposition(status) === 'POLL'
  },

  schedulePoll(delayMs: number) {
    if (!this.visible || !this.shouldPoll() || this.pollTimer) {
      return
    }
    const delay = Number.isFinite(delayMs)
      ? Math.min(60000, Math.max(0, Math.trunc(delayMs)))
      : 1000
    this.pollTimer = setTimeout(() => {
      this.pollTimer = undefined
      void this.pollOnce()
    }, delay)
  },

  clearPollTimer() {
    if (!this.pollTimer) return
    clearTimeout(this.pollTimer)
    this.pollTimer = undefined
  },

  async pollOnce() {
    const intent = this.intent
    if (!this.visible || !intent?.operationUid || this.polling) return
    this.polling = true
    try {
      const projection = await cleanOperation(intent.operationUid, false)
      this.intent = projectCleanOperationIntent(intent, projection)
      this.setData({ cleanRecordNo: projection.cleanRecordNo || '' })
      this.applyStatus(projection.status)
      if (
        projection.status === 'PREPARED'
        || projection.status === 'EDGE_SAVED'
        || projection.status === 'IN_PROGRESS'
      ) {
        this.schedulePoll(
          projection.recommendedPollAfterMs
          ?? this.intent.recommendedPollAfterMs,
        )
      }
    } catch {
      if (this.visible) {
        this.setData({
          stage: 'poll-paused',
          statusTitle: '状态查询已暂停',
          statusDescription: '网络异常或服务返回了未知状态。原清运操作仍被保留，可继续查询，不能重新发起另一笔相同操作。',
        })
      }
    } finally {
      this.polling = false
    }
  },

  applyStatus(status: CleanOperationStatus) {
    const copy = statusCopy(status)
    const stage = status === 'COMPLETED'
      ? 'completed'
      : status === 'PRE_UNLOCK_ENDED'
        ? 'ended'
        : status === 'ABORTED'
          ? 'aborted'
        : status === 'RECOVERY_REQUIRED'
          ? 'recovery'
          : 'polling'
    this.setData({
      stage,
      statusTitle: copy.title,
      statusDescription: copy.description,
    })
    if (
      status === 'COMPLETED'
      || status === 'PRE_UNLOCK_ENDED'
      || status === 'ABORTED'
    ) {
      forgetCleanOperationIntent()
      this.intent = null
      this.clearPollTimer()
    }
    if (status === 'RECOVERY_REQUIRED') this.clearPollTimer()
  },

  onBackHome() {
    wx.switchTab({ url: '/pages/clean/clean' })
  },
})
