import {
  getDeliveryOptions,
  getDeliverySession,
  startDeliverySession,
} from '../../api/delivery'
import { FEATURES } from '../../config/index'
import {
  ensureLoggedIn,
  getSession,
  isSessionExpired,
  refreshSession,
  routeToEntry,
} from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'
import {
  captureOrdinaryDeviceEntryFromQuery,
  completePendingDeviceEntry,
  dismissPendingDeviceEntry,
  dismissUnstartedPendingDeviceEntry,
  markPendingDeviceStartAttempt,
  markPendingDeviceEntryStarted,
  peekPendingDeviceEntry,
  preparePendingDeviceStart,
  releasePendingDeviceStart,
  routePendingDeviceEntry,
  type PendingDeviceEntry,
} from '../../utils/device-entry-intent'
import { requestPhoneBindingBeforeAction } from '../../utils/phone-binding-prompt'
import { MiniappApiProblem } from '../../utils/request'
import type {
  DeliveryOptionBlocker,
  DeliveryPortOption,
  DeliverySessionPhase,
  DeliverySessionView,
} from '../../types/api'

interface DeliveryPortCard extends DeliveryPortOption {
  nameText: string
  priceText: string
  fullnessText: string
  blockerText: string
}

class StaleDeviceEntryError extends Error {
  constructor() {
    super('扫码入口已更新')
    this.name = 'StaleDeviceEntryError'
  }
}

function requirePendingEntry(entryId: string): PendingDeviceEntry {
  const entry = peekPendingDeviceEntry()
  if (!entry || entry.entryId !== entryId) {
    throw new StaleDeviceEntryError()
  }
  return entry
}

const BLOCKER_TEXT: Record<DeliveryOptionBlocker, string> = {
  PHONE_BINDING_REQUIRED: '请先验证手机号',
  WALLET_DELIVERY_LIMIT_REACHED: '账户余额已达到停投限制',
  DEPLOYMENT_NOT_ENABLED: '设备尚未启用',
  BUSINESS_SWITCH_DISABLED: '设备暂停接收投递',
  CONFIGURATION_NOT_APPLIED: '设备配置尚未生效',
  EDGE_OFFLINE: '设备当前离线',
  SAFETY_LOCKED: '设备处于安全锁定状态',
  DEVICE_BUSY: '设备正在执行其他作业',
  PORT_DISABLED: '投口已停用',
  PORT_SENSOR_UNHEALTHY: '投口传感器状态异常',
  DELIVERY_RESULT_PENDING: '上一笔投递结果仍在处理中',
  CURRENT_BAG_MISSING: '投口尚未安装有效垃圾袋',
  BASELINE_REMEASUREMENT_ACTIVE: '投口正在重测重量基准',
  PORT_FULL: '投口已满',
  PORT_CLEAN_OPERATION_ACTIVE: '投口正在清运',
}

const PHASE_TEXT: Record<DeliverySessionPhase, string> = {
  START_QUEUED: '投递请求已受理，正在等待设备响应',
  IN_PROGRESS: '投递已开始，请按照设备屏幕提示操作',
  FINAL_RESULT_PENDING: '投递已结束，正在生成投递结果',
  RECOVERY_REQUIRED: '设备结果需要恢复处理，请勿重复扫码',
  BUSINESS_CONFIRMED: '投递已完成',
  PRE_START_FAILED: '设备未能开始本次投递',
}

// 409 可能是同幂等键并发中的败者，或原请求刚成功后的 ACTIVE 冲突，
// 不能释放 key。以下状态才足以证明本次物理意图未被受理。
const DEFINITE_REJECTION_STATUS = new Set([400, 401, 403, 404, 422])

function portCard(port: DeliveryPortOption): DeliveryPortCard {
  return {
    ...port,
    nameText: port.displayName || `${port.portNo} 号投口`,
    priceText: port.unitPriceYuanPerKg
      ? `¥${port.unitPriceYuanPerKg}/kg`
      : '价格待确认',
    fullnessText: port.fullnessPercent
      ? `满溢度 ${port.fullnessPercent}%`
      : '满溢度待确认',
    blockerText: port.blockers
      .map((blocker) => BLOCKER_TEXT[blocker] || blocker)
      .join('；'),
  }
}

function pollDelay(value: number | null | undefined): number {
  if (!Number.isFinite(value)) return 1000
  return Math.min(60000, Math.max(250, Math.trunc(value as number)))
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

Page({
  advancing: false,
  starting: false,
  polling: false,
  pageVisible: false,
  terminal: false,
  displayedEntryId: undefined as string | undefined,

  data: {
    state: 'loading',
    deviceName: '正在识别设备',
    address: '',
    message: '请稍候',
    ports: [] as DeliveryPortCard[],
    sessionUid: '',
    orderNo: '',
    entryId: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    captureOrdinaryDeviceEntryFromQuery(options)
  },

  onShow() {
    this.pageVisible = true
    const pending = peekPendingDeviceEntry()
    if (pending && pending.entryId !== this.displayedEntryId) {
      this.terminal = false
      this.setData({
        state: 'loading',
        deviceName: '正在识别设备',
        address: '',
        message: '请稍候',
        ports: [],
        sessionUid: '',
        orderNo: '',
        entryId: '',
      })
    }
    if (!this.terminal) void this.advancePendingEntry()
  },

  onHide() {
    this.pageVisible = false
  },

  onUnload() {
    this.pageVisible = false
  },

  async advancePendingEntry() {
    if (this.advancing || this.starting) return
    this.advancing = true
    let restartLatest = false
    try {
      const entry = peekPendingDeviceEntry()
      if (!entry) {
        this.setData({
          state: 'invalid',
          deviceName: '未识别到设备',
          message: '请重新扫描设备上的 EcoBin 二维码',
          ports: [],
          entryId: '',
        })
        return
      }
      this.displayedEntryId = entry.entryId

      if (entry.accepted) {
        this.showAccepted(entry)
        void this.pollSession(entry)
        return
      }

      const cachedSession = getSession()
      if (!cachedSession || isSessionExpired(cachedSession)) {
        wx.reLaunch({ url: '/pages/login/login' })
        return
      }

      const session = await ensureLoggedIn({
        deploymentCode: entry.deploymentCode,
      })
      requirePendingEntry(entry.entryId)
      if (
        session.audience !== 'miniapp'
        || session.entryMode !== 'USER'
      ) {
        dismissPendingDeviceEntry(entry.entryId)
        wx.showToast({
          title: '当前账号不能进行用户投递',
          icon: 'none',
        })
        routeToEntry(session)
        return
      }

      if (
        requestPhoneBindingBeforeAction(
          session,
          () => {
            const current = getSession()
            if (!routePendingDeviceEntry(current)) routeToEntry(current)
          },
          () => dismissUnstartedPendingDeviceEntry(entry.entryId),
        )
      ) {
        this.setData({
          state: 'phone',
          deviceName: '需要验证手机号',
          message: '完成手机号验证后将自动继续本次投递',
          ports: [],
        })
        return
      }

      await this.loadOptions(entry)
    } catch (error) {
      if (error instanceof StaleDeviceEntryError) {
        restartLatest = true
      } else {
        this.showError(error)
      }
    } finally {
      this.advancing = false
    }
    if (restartLatest && this.pageVisible) void this.advancePendingEntry()
  },

  async loadOptions(entry: PendingDeviceEntry) {
    this.setData({
      state: 'loading',
      deviceName: '正在读取设备',
      message: '正在检查可用投口',
      ports: [],
    })
    const options = await getDeliveryOptions(entry.deploymentCode)
    requirePendingEntry(entry.entryId)
    if (options.deploymentCode !== entry.deploymentCode) {
      throw new Error('设备响应与二维码不一致')
    }

    const ports = options.ports.map(portCard)
    const available = ports.filter((port) => port.deliveryAllowed)
    this.setData({
      deviceName: options.displayName || 'EcoBin 智能回收设备',
      address: options.address || '',
      ports,
      entryId: entry.entryId,
    })

    // POST 发出后即使网络结果未知，也只能用原投口和原幂等键恢复。
    if (
      entry.idempotencyKey
      && Number.isInteger(entry.selectedPortNo)
    ) {
      await this.startPort(entry.entryId, entry.selectedPortNo as number)
      return
    }

    if (available.length === 1) {
      await this.startPort(entry.entryId, available[0].portNo)
      return
    }
    if (available.length > 1) {
      this.setData({
        state: 'selecting',
        message: '请选择本次投递使用的投口',
      })
      return
    }
    this.setData({
      state: 'blocked',
      message: options.deviceBusy
        ? '设备正在执行其他作业，请稍后再试'
        : '当前没有可用投口',
    })
  },

  onPortTap(event: WechatMiniprogram.TouchEvent) {
    if (this.data.state !== 'selecting') return
    const entryId = String(event.currentTarget.dataset.entryId || '')
    const current = peekPendingDeviceEntry()
    if (!entryId || current?.entryId !== entryId) {
      this.setData({
        state: 'loading',
        message: '扫码入口已更新，正在重新读取设备',
        ports: [],
        entryId: '',
      })
      void this.advancePendingEntry()
      return
    }
    const portNo = Number(event.currentTarget.dataset.portNo)
    const port = (this.data.ports as DeliveryPortCard[])
      .find((item) => item.portNo === portNo)
    if (!port) return
    if (!port.deliveryAllowed) {
      wx.showToast({
        title: port.blockerText || '该投口当前不可用',
        icon: 'none',
      })
      return
    }
    void this.startPort(entryId, portNo)
  },

  async startPort(entryId: string, portNo: number) {
    if (this.starting) return
    this.starting = true
    let deploymentCode: string | undefined
    let restartLatest = false
    let attemptNumber = 0
    let attemptedKey: string | undefined
    try {
      const entry = requirePendingEntry(entryId)
      deploymentCode = entry.deploymentCode
      if (
        entry.idempotencyKey
        && entry.selectedPortNo !== portNo
      ) {
        throw new Error('正在恢复原投递请求，不能更换投口')
      }
      let idempotencyKey = entry.selectedPortNo === portNo
        ? entry.idempotencyKey
        : undefined
      if (!idempotencyKey) idempotencyKey = await createIdempotencyKey()
      const prepared = preparePendingDeviceStart(
        entryId,
        portNo,
        idempotencyKey,
      )
      if (!prepared?.idempotencyKey) {
        if (peekPendingDeviceEntry()?.entryId !== entryId) {
          throw new StaleDeviceEntryError()
        }
        throw new Error('无法保存投递请求，请重新扫码')
      }
      const attempted = markPendingDeviceStartAttempt(entryId)
      if (!attempted?.idempotencyKey) {
        if (peekPendingDeviceEntry()?.entryId !== entryId) {
          throw new StaleDeviceEntryError()
        }
        throw new Error('无法保存投递状态，本次请求尚未发出')
      }
      attemptNumber = attempted.startAttemptCount ?? 0
      attemptedKey = attempted.idempotencyKey

      this.setData({
        state: 'starting',
        message: '正在提交投递请求，请勿重复操作',
      })
      const accepted = await startDeliverySession(
        attempted.deploymentCode,
        portNo,
        attempted.idempotencyKey,
      )
      const started = markPendingDeviceEntryStarted(entryId, accepted)
      if (!started) throw new Error('无法保存投递会话状态')
      this.showAccepted(started)
      void this.pollSession(started)
    } catch (error) {
      const recovered = peekPendingDeviceEntry()
      if (recovered?.entryId === entryId && recovered.accepted) {
        this.showAccepted(recovered)
        void this.pollSession(recovered)
        return
      }
      if (error instanceof StaleDeviceEntryError) {
        restartLatest = true
      } else if (
        error instanceof MiniappApiProblem
        && error.code === 'IDENTITY.PHONE_BINDING_REQUIRED'
        && deploymentCode
      ) {
        const session = await refreshSession({
          deploymentCode,
        }).catch(() => undefined)
        const firstAttemptReleased = attemptNumber === 1 && attemptedKey
          ? !!releasePendingDeviceStart(
            entryId,
            portNo,
            attemptedKey,
            attemptNumber,
          )
          : false
        if (
          session
          && requestPhoneBindingBeforeAction(
            session,
            () => {
              const current = getSession()
              if (!routePendingDeviceEntry(current)) routeToEntry(current)
            },
            () => {
              if (firstAttemptReleased) {
                dismissUnstartedPendingDeviceEntry(entryId)
              }
            },
          )
        ) {
          this.setData({
            state: 'phone',
            message: '完成手机号验证后将自动继续本次投递',
          })
          return
        }
      }
      if (!restartLatest) {
        if (
          error instanceof MiniappApiProblem
          && attemptNumber === 1
          && !!attemptedKey
          && DEFINITE_REJECTION_STATUS.has(error.status)
        ) {
          releasePendingDeviceStart(
            entryId,
            portNo,
            attemptedKey as string,
            attemptNumber,
          )
        }
        this.showError(error)
      }
    } finally {
      this.starting = false
    }
    if (restartLatest && this.pageVisible) void this.advancePendingEntry()
  },

  showAccepted(entry: PendingDeviceEntry) {
    const accepted = entry.accepted
    if (!accepted) return
    this.setData({
      state: 'active',
      message: PHASE_TEXT[accepted.phase],
      sessionUid: accepted.sessionUid,
    })
  },

  async pollSession(entry: PendingDeviceEntry) {
    if (this.polling || !entry.accepted) return
    this.polling = true
    let waitMs = pollDelay(entry.accepted.recommendedPollAfterMs)
    try {
      while (this.pageVisible) {
        await delay(waitMs)
        if (!this.pageVisible) return
        const current = peekPendingDeviceEntry()
        if (
          !current
          || current.entryId !== entry.entryId
          || !current.accepted
        ) {
          return
        }
        const session = await getDeliverySession(
          current.accepted.sessionUid,
          current.deploymentCode,
        )
        this.presentSession(session)
        if (session.status !== 'ACTIVE') {
          completePendingDeviceEntry(entry.entryId)
          return
        }
        waitMs = pollDelay(session.recommendedPollAfterMs)
      }
    } catch (error) {
      if (this.pageVisible) this.showError(error)
    } finally {
      this.polling = false
    }
  },

  presentSession(session: DeliverySessionView) {
    if (session.status === 'COMPLETED') {
      this.terminal = true
      this.setData({
        state: 'completed',
        message: PHASE_TEXT[session.phase],
        orderNo: session.deliveryOrderNo || '',
      })
      return
    }
    if (session.status === 'ENDED') {
      this.terminal = true
      this.setData({
        state: 'ended',
        message: PHASE_TEXT[session.phase],
      })
      return
    }
    this.setData({
      state: 'active',
      message: PHASE_TEXT[session.phase],
    })
  },

  showError(error: unknown) {
    const message = error instanceof Error && error.message
      ? error.message
      : '暂时无法开始投递，请稍后重试'
    this.setData({
      state: 'error',
      message,
    })
  },

  onRetry() {
    void this.advancePendingEntry()
  },

  onOpenOrders() {
    if (!FEATURES.targetDeliveryOrderApi) {
      wx.showToast({ title: '投递订单服务正在接入', icon: 'none' })
      return
    }
    const orderNo = String(this.data.orderNo || '')
    wx.navigateTo({
      url: orderNo
        ? `/pages/order-detail/order-detail?deliveryOrderNo=${
          encodeURIComponent(orderNo)
        }`
        : '/pages/orders/orders',
    })
  },

  onBackHome() {
    const entry = peekPendingDeviceEntry()
    // 一旦生成幂等键，请求可能已到达服务端；即使网络结果未知也必须保留，
    // 以便重新进入时使用同一个键恢复，不能再次触发物理动作。
    if (entry && !entry.idempotencyKey && !entry.accepted) {
      dismissPendingDeviceEntry(entry.entryId)
    }
    wx.switchTab({ url: '/pages/home/home' })
  },
})
