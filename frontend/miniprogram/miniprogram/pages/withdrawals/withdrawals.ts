import { bindCurrentPhone } from '../../api/auth'
import {
  createMerchantTransferAuthorization,
  createWithdrawal,
  merchantTransferAuthorization,
  merchantTransferConfirmation,
  myWithdrawal,
  myWithdrawals,
  queryMerchantTransferAuthorization,
  withdrawalConfiguration,
} from '../../api/withdrawal'
import { myWallet } from '../../api/wallet'
import { getSession, markPhoneBound } from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'
import {
  formatMoneyCny,
  isMoneyInputDraft,
  normalizeMoneyInput,
} from '../../utils/decimal'
import { formatLocalDateTime } from '../../utils/local-time'
import {
  cancelAfterPhoneBindingPrompt,
  continueAfterPhoneBindingPrompt,
  requestPhoneBindingBeforeAction,
} from '../../utils/phone-binding-prompt'
import {
  isWechatPhoneGrantCancelled,
  reportPhoneBindingError,
  reportWechatPhoneGrantError,
  type WechatPhoneGrantDetail,
} from '../../utils/phone-grant'
import { MiniappApiProblem } from '../../utils/request'
import {
  clearPendingWithdrawalIntent,
  rememberPendingWithdrawalIntent,
  restorePendingWithdrawalIntent,
  type PendingWithdrawalIntent,
} from '../../utils/withdrawal-intent'
import {
  validateWithdrawalAmount,
  type WithdrawalAmountError,
} from '../../utils/withdrawal-validation'
import type {
  MerchantTransferAuthorizationView,
  WithdrawalConfigurationView,
  WithdrawalStatus,
  WithdrawalView,
} from '../../types/api'

interface WithdrawalListItem extends WithdrawalView {
  amountText: string
  createdText: string
  statusText: string
  statusTone: string
  channelStateText: string
}

interface MerchantTransferOptions {
  mchId: string
  appId: string
  package: string
  success: (result: { errMsg: string }) => void
  fail: (result: { errMsg: string }) => void
}

type MerchantTransferWx = typeof wx & {
  requestMerchantTransfer(options: MerchantTransferOptions): void
}

const PAGE_SIZE = 20
const POLL_DELAYS_MS = [2000, 3000, 5000]
const MAX_POLL_ELAPSED_MS = 60_000
const AUTHORIZATION_POLL_DELAYS_MS = [1000, 1500, 2000, 3000]
const MAX_AUTHORIZATION_POLL_ELAPSED_MS = 45_000

const TERMINAL_STATUSES = new Set<WithdrawalStatus>([
  'SUCCEEDED',
  'REJECTED',
  'LOCAL_CANCELLED',
  'LOCAL_ABORTED_BEFORE_CHANNEL',
  'CHANNEL_FAILED',
  'CHANNEL_CANCELLED',
])

const STATUS: Record<string, [string, string]> = {
  PENDING_REVIEW: ['等待人工审核', 'warning'],
  READY_TO_SUBMIT: ['审核通过，准备转账', 'processing'],
  CHANNEL_PROCESSING: ['微信转账处理中', 'processing'],
  SUCCEEDED: ['提现成功', 'success'],
  REJECTED: ['审核未通过', 'failed'],
  LOCAL_CANCELLED: ['已取消', 'neutral'],
  LOCAL_ABORTED_BEFORE_CHANNEL: ['渠道前已终止', 'neutral'],
  CHANNEL_FAILED: ['微信转账失败', 'failed'],
  CHANNEL_CANCELLED: ['微信转账已撤销', 'neutral'],
}

const CHANNEL_STATE: Record<string, string> = {
  ACCEPTED: '微信已受理',
  PROCESSING: '微信处理中',
  TRANSFERING: '微信转账中',
  CANCELING: '微信撤销处理中',
  WAIT_USER_CONFIRM: '等待确认收款',
  SUCCESS: '微信转账成功',
  FAIL: '微信转账失败',
  CANCELLED: '微信转账已撤销',
}

function listItem(item: WithdrawalView): WithdrawalListItem {
  const [statusText, statusTone] = STATUS[item.status]
    ?? [item.status, 'neutral']
  return {
    ...item,
    amountText: formatMoneyCny(item.amountYuan),
    createdText: formatLocalDateTime(item.createdAt),
    statusText,
    statusTone,
    channelStateText: item.channelState
      ? CHANNEL_STATE[item.channelState] ?? item.channelState
      : '',
  }
}

function mergeItems(
  current: WithdrawalListItem[],
  incoming: WithdrawalListItem[],
): WithdrawalListItem[] {
  const byNumber = new Map<string, WithdrawalListItem>()
  for (const item of current.concat(incoming)) {
    byNumber.set(item.withdrawalNo, item)
  }
  return Array.from(byNumber.values())
}

function errorText(error: unknown): string {
  if (error instanceof MiniappApiProblem) return error.message
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

function displayMoney(value: string): string {
  const formatted = formatMoneyCny(value)
  return formatted === '—' ? '—' : `¥${formatted}`
}

function validationText(
  error: WithdrawalAmountError,
  configuration: WithdrawalConfigurationView | null,
  availableBalanceYuan: string | null,
): string {
  switch (error) {
    case 'INVALID_AMOUNT':
      return '请输入金额，最多保留两位小数'
    case 'ZERO_AMOUNT':
      return '提现金额必须大于 0 元'
    case 'CONFIGURATION_UNAVAILABLE':
      return '提现规则暂不可用，请重新加载'
    case 'BELOW_MINIMUM':
      return configuration
        ? `最低提现金额为 ${displayMoney(configuration.manualMinimumYuan)}`
        : '提现金额低于最低限额'
    case 'ABOVE_MAXIMUM':
      return configuration
        ? `单笔提现最多 ${displayMoney(configuration.manualMaximumYuan)}`
        : '提现金额超过单笔限额'
    case 'BALANCE_UNAVAILABLE':
      return '可用余额暂不可用，请重新加载'
    case 'BALANCE_INSUFFICIENT':
      return availableBalanceYuan
        ? `提现金额不能超过可用余额 ${displayMoney(availableBalanceYuan)}`
        : '可用余额不足'
  }
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

Page({
  requestGeneration: 0,
  pollGeneration: 0,
  authorizationPollGeneration: 0,
  cursorRecoveryUsed: false,
  phoneBindingIntentKey: '',
  createIntent: null as PendingWithdrawalIntent | null,

  data: {
    amountYuan: '',
    amountError: '',
    canSubmit: false,
    availableBalanceYuan: null as string | null,
    availableBalanceText: '—',
    configuration: null as WithdrawalConfigurationView | null,
    authorization: null as MerchantTransferAuthorizationView | null,
    authorizationLoading: true,
    authorizationError: '',
    authorizationSubmitting: false,
    eligibilityLoading: true,
    eligibilityError: '',
    items: [] as WithdrawalListItem[],
    nextCursor: null as string | null,
    listLoading: true,
    loadingMore: false,
    listError: '',
    finished: false,
    initialLoading: true,
    submitting: false,
    confirmingWithdrawalNo: '',
    pollingWithdrawalNo: '',
    showPhoneGrant: false,
    phoneGrantSubmitting: false,
  },

  onLoad() {
    const session = getSession()
    const pending = session
      ? restorePendingWithdrawalIntent(session.subjectUid)
      : null
    if (pending) {
      this.createIntent = pending
      this.setData({ amountYuan: pending.amountYuan })
    }
    void this.reload()
  },

  onShow() {
    if (
      !this.data.initialLoading
      && !this.data.submitting
      && !this.data.authorizationSubmitting
    ) {
      void this.reload()
    }
  },

  onPullDownRefresh() {
    void this.reload(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    void this.loadMore()
  },

  onUnload() {
    this.requestGeneration += 1
    this.pollGeneration += 1
    this.authorizationPollGeneration += 1
    cancelAfterPhoneBindingPrompt()
  },

  async reload(
    done?: () => void,
    preserveCursorRecovery = false,
  ) {
    if (!preserveCursorRecovery) this.cursorRecoveryUsed = false
    const generation = ++this.requestGeneration
    this.setData({
      eligibilityLoading: true,
      eligibilityError: '',
      canSubmit: false,
      authorizationLoading: true,
      authorizationError: '',
      listLoading: true,
      loadingMore: false,
      listError: '',
      items: [],
      nextCursor: null,
      finished: false,
    })
    try {
      await Promise.all([
        this.loadEligibility(generation),
        this.loadAuthorization(generation),
        this.fetchPage(undefined, generation),
      ])
    } finally {
      if (generation === this.requestGeneration) {
        this.setData({ initialLoading: false })
      }
      done?.()
    }
  },

  async loadEligibility(generation: number) {
    try {
      const [wallet, configuration] = await Promise.all([
        myWallet(false),
        withdrawalConfiguration(false),
      ])
      if (generation !== this.requestGeneration) return
      this.setData({
        availableBalanceYuan: wallet.availableBalanceYuan,
        availableBalanceText: displayMoney(wallet.availableBalanceYuan),
        configuration,
        eligibilityError: '',
      })
    } catch (error) {
      if (generation !== this.requestGeneration) return
      this.setData({
        availableBalanceYuan: null,
        availableBalanceText: '—',
        configuration: null,
        eligibilityError: errorText(error),
        canSubmit: false,
      })
    } finally {
      if (generation === this.requestGeneration) {
        this.setData(
          { eligibilityLoading: false },
          () => this.refreshAmountValidation(),
        )
      }
    }
  },

  async loadAuthorization(generation: number) {
    try {
      const authorization = await merchantTransferAuthorization(false)
      if (generation !== this.requestGeneration) return
      this.setData({
        authorization,
        authorizationError: '',
      })
    } catch (error) {
      if (generation !== this.requestGeneration) return
      this.setData({
        authorization: null,
        authorizationError: errorText(error),
        canSubmit: false,
      })
    } finally {
      if (generation === this.requestGeneration) {
        this.setData(
          { authorizationLoading: false },
          () => this.refreshAmountValidation(),
        )
      }
    }
  },

  async fetchPage(cursor?: string, generation?: number) {
    const activeGeneration = generation ?? this.requestGeneration
    if (activeGeneration !== this.requestGeneration) return
    if (cursor ? this.data.loadingMore : false) return
    if (cursor) this.setData({ loadingMore: true })
    let recoverInvalidCursor = false
    try {
      const page = await myWithdrawals({
        cursor,
        limit: PAGE_SIZE,
      }, false)
      if (activeGeneration !== this.requestGeneration) return
      const incoming = page.items.map(listItem)
      this.setData({
        items: cursor
          ? mergeItems(this.data.items, incoming)
          : incoming,
        nextCursor: page.nextCursor,
        finished: !page.nextCursor,
        listError: '',
      })
    } catch (error) {
      if (activeGeneration !== this.requestGeneration) return
      if (
        cursor
        && !this.cursorRecoveryUsed
        && error instanceof MiniappApiProblem
        && error.code === 'COMMON.INVALID_CURSOR'
      ) {
        this.cursorRecoveryUsed = true
        recoverInvalidCursor = true
      } else if (!cursor) {
        this.setData({ listError: errorText(error) })
      } else {
        wx.showToast({ title: '更多提现记录加载失败', icon: 'none' })
      }
    } finally {
      if (activeGeneration === this.requestGeneration) {
        this.setData({ listLoading: false, loadingMore: false })
      }
    }
    if (
      recoverInvalidCursor
      && activeGeneration === this.requestGeneration
    ) {
      wx.showToast({ title: '提现记录已更新', icon: 'none' })
      await this.reload(undefined, true)
    }
  },

  async loadMore() {
    if (
      this.data.listLoading
      || this.data.loadingMore
      || this.data.finished
      || !this.data.nextCursor
    ) return
    await this.fetchPage(this.data.nextCursor)
  },

  refreshAmountValidation() {
    if (!this.data.amountYuan || this.data.eligibilityLoading) {
      this.setData({ amountError: '', canSubmit: false })
      return
    }
    const result = validateWithdrawalAmount(
      this.data.amountYuan,
      this.data.configuration,
      this.data.availableBalanceYuan,
    )
    this.setData({
      amountError: result.ok
        ? ''
        : validationText(
          result.error,
          this.data.configuration,
          this.data.availableBalanceYuan,
        ),
      canSubmit: result.ok
        && !this.data.eligibilityLoading
        && !this.data.authorizationLoading
        && !this.data.authorizationSubmitting
        && this.data.authorization?.status === 'ACTIVE',
    })
  },

  onAmountInput(event: WechatMiniprogram.Input): string {
    const next = String(event.detail.value)
    if (!isMoneyInputDraft(next)) {
      this.setData({
        amountError: '金额最多只能输入两位小数',
        canSubmit: false,
      })
      return this.data.amountYuan
    }
    this.setData({ amountYuan: next }, () => this.refreshAmountValidation())
    return next
  },

  onAmountBlur() {
    const normalized = normalizeMoneyInput(this.data.amountYuan)
    if (!normalized) return
    this.setData(
      { amountYuan: normalized },
      () => this.refreshAmountValidation(),
    )
  },

  onAuthorizationAction() {
    if (this.data.authorizationSubmitting) return
    const proceed = () => void this.startAuthorizationAction()
    if (requestPhoneBindingBeforeAction(getSession(), proceed)) return
    proceed()
  },

  async startAuthorizationAction() {
    if (this.data.authorizationSubmitting) return
    this.setData({
      authorizationSubmitting: true,
      authorizationError: '',
    })
    try {
      let current = this.data.authorization
        ?? await merchantTransferAuthorization(false)
      this.setAuthorization(current)
      if (
        current.status === 'NOT_OPENED'
        || current.status === 'CLOSED'
        || current.status === 'EXPIRED'
        || current.status === 'FAILED'
      ) {
        await createMerchantTransferAuthorization(
          await createIdempotencyKey(),
        )
        current = await this.pollAuthorizationUntil(
          (item) => item.status !== 'PREPARING',
          '微信授权申请仍在准备中，请稍后重试',
        )
      }
      if (current.status === 'ACTIVE') {
        wx.showToast({ title: '授权已完成', icon: 'success' })
        return
      }
      if (current.status === 'PREPARING') {
        current = await this.pollAuthorizationUntil(
          (item) => item.status !== 'PREPARING',
          '微信授权申请仍在准备中，请稍后重试',
        )
      }
      if (current.status === 'FAILED') {
        throw new Error('微信仍未受理授权申请，请检查后重新申请')
      }
      if (current.status === 'UNKNOWN') {
        await queryMerchantTransferAuthorization(
          await createIdempotencyKey(),
        )
        current = await this.pollAuthorizationUntil(
          (item) => item.status !== 'UNKNOWN',
          '授权状态仍在核对，请稍后再试',
        )
      }
      if (current.status !== 'WAIT_USER_CONFIRM') {
        if (current.status === 'ACTIVE') {
          wx.showToast({ title: '授权已完成', icon: 'success' })
          return
        }
        throw new Error('当前授权已结束，请重新发起授权')
      }
      await this.refreshAndOpenAuthorization(current)
    } catch (error) {
      const message = errorText(error)
      this.setData({ authorizationError: message })
      wx.showToast({ title: message, icon: 'none' })
    } finally {
      this.setData({ authorizationSubmitting: false })
      this.refreshAmountValidation()
    }
  },

  async refreshAndOpenAuthorization(
    current: MerchantTransferAuthorizationView,
  ) {
    const previousQueryAt = current.lastSuccessfulQueryAt
    await queryMerchantTransferAuthorization(await createIdempotencyKey())
    const refreshed = await this.pollAuthorizationUntil(
      (item) => item.status !== 'WAIT_USER_CONFIRM'
        || (
          item.lastSuccessfulQueryAt !== null
          && item.lastSuccessfulQueryAt !== previousQueryAt
        ),
      '微信授权参数刷新超时，请稍后重试',
    )
    if (refreshed.status === 'ACTIVE') {
      wx.showToast({ title: '授权已完成', icon: 'success' })
      return
    }
    if (
      refreshed.status !== 'WAIT_USER_CONFIRM'
      || !refreshed.confirmationRequired
      || !refreshed.appId
      || !refreshed.mchId
      || !refreshed.packageInfo
    ) {
      throw new Error('微信授权参数当前不可用，请稍后重试')
    }
    const currentAppId = wx.getAccountInfoSync().miniProgram.appId
    if (refreshed.appId !== currentAppId) {
      throw new Error('当前小程序与授权 AppID 不一致，已停止调起授权页')
    }
    if (!wx.canIUse('requestMerchantTransfer')) {
      throw new Error('当前微信版本过低，请更新后再开通自动收款')
    }
    const returned = await this.requestAuthorizationPage(refreshed)
    if (!returned) {
      wx.showToast({ title: '你暂未完成自动收款授权', icon: 'none' })
      return
    }
    const beforeConfirmationQuery = refreshed.lastSuccessfulQueryAt
    await queryMerchantTransferAuthorization(await createIdempotencyKey())
    const confirmed = await this.pollAuthorizationUntil(
      (item) => item.status !== 'WAIT_USER_CONFIRM'
        || (
          item.lastSuccessfulQueryAt !== null
          && item.lastSuccessfulQueryAt !== beforeConfirmationQuery
        ),
      '微信授权结果仍在核对，请稍后下拉刷新',
    )
    if (confirmed.status === 'ACTIVE') {
      wx.showToast({ title: '授权已完成', icon: 'success' })
    } else if (confirmed.status === 'WAIT_USER_CONFIRM') {
      wx.showToast({ title: '尚未确认授权，可稍后继续', icon: 'none' })
    } else {
      throw new Error('微信授权未生效，请重新发起授权')
    }
  },

  requestAuthorizationPage(
    authorization: MerchantTransferAuthorizationView,
  ): Promise<boolean> {
    return new Promise((resolve, reject) => {
      const merchantWx = wx as MerchantTransferWx
      merchantWx.requestMerchantTransfer({
        mchId: authorization.mchId as string,
        appId: authorization.appId as string,
        package: authorization.packageInfo as string,
        success: () => resolve(true),
        fail: (result) => {
          if (result.errMsg.includes('cancel')) {
            resolve(false)
            return
          }
          reject(new Error('微信自动收款授权页调起失败'))
        },
      })
    })
  },

  async pollAuthorizationUntil(
    completed: (item: MerchantTransferAuthorizationView) => boolean,
    timeoutMessage: string,
  ): Promise<MerchantTransferAuthorizationView> {
    const generation = ++this.authorizationPollGeneration
    const startedAt = Date.now()
    let delayIndex = 0
    while (
      generation === this.authorizationPollGeneration
      && Date.now() - startedAt < MAX_AUTHORIZATION_POLL_ELAPSED_MS
    ) {
      const delay = AUTHORIZATION_POLL_DELAYS_MS[Math.min(
        delayIndex,
        AUTHORIZATION_POLL_DELAYS_MS.length - 1,
      )]
      delayIndex += 1
      await wait(delay)
      if (generation !== this.authorizationPollGeneration) {
        throw new Error('授权状态读取已停止')
      }
      const current = await merchantTransferAuthorization(false)
      this.setAuthorization(current)
      if (completed(current)) return current
    }
    throw new Error(timeoutMessage)
  },

  setAuthorization(authorization: MerchantTransferAuthorizationView) {
    this.setData({
      authorization,
      authorizationLoading: false,
      authorizationError: '',
    }, () => this.refreshAmountValidation())
  },

  onCreate() {
    if (this.data.submitting) return
    if (this.data.authorization?.status !== 'ACTIVE') {
      wx.showToast({ title: '请先完成收款授权', icon: 'none' })
      return
    }
    const result = validateWithdrawalAmount(
      this.data.amountYuan,
      this.data.configuration,
      this.data.availableBalanceYuan,
    )
    if (!result.ok) {
      wx.showToast({
        title: validationText(
          result.error,
          this.data.configuration,
          this.data.availableBalanceYuan,
        ),
        icon: 'none',
      })
      return
    }
    const submit = () => void this.submitWithdrawal(result.amountYuan)
    if (requestPhoneBindingBeforeAction(getSession(), submit)) return
    submit()
  },

  async submitWithdrawal(amountYuan: string) {
    if (this.data.submitting) return
    const session = getSession()
    if (!session || session.audience !== 'miniapp') {
      wx.showToast({ title: '登录状态已失效，请重新进入', icon: 'none' })
      return
    }
    const validation = validateWithdrawalAmount(
      amountYuan,
      this.data.configuration,
      this.data.availableBalanceYuan,
    )
    if (!validation.ok) {
      wx.showToast({
        title: validationText(
          validation.error,
          this.data.configuration,
          this.data.availableBalanceYuan,
        ),
        icon: 'none',
      })
      return
    }
    const canonicalAmount = validation.amountYuan
    this.setData({ submitting: true, amountYuan: canonicalAmount })
    try {
      const pending = this.createIntent
        ?? restorePendingWithdrawalIntent(session.subjectUid)
      const reusable = pending?.subjectUid === session.subjectUid
        && pending.amountYuan === canonicalAmount
      const intent: PendingWithdrawalIntent = reusable
        ? pending
        : {
            idempotencyKey: await createIdempotencyKey(),
            amountYuan: canonicalAmount,
            subjectUid: session.subjectUid,
            createdAt: Date.now(),
          }
      this.createIntent = intent
      rememberPendingWithdrawalIntent(intent)
      await createWithdrawal(canonicalAmount, intent.idempotencyKey)
      this.createIntent = null
      clearPendingWithdrawalIntent()
      this.setData({ amountYuan: '', amountError: '', canSubmit: false })
      wx.showToast({ title: '提现申请已提交', icon: 'success' })
      await this.reload()
    } catch (error) {
      wx.showToast({ title: errorText(error), icon: 'none' })
      if (!(error instanceof MiniappApiProblem) || !error.retryable) {
        this.createIntent = null
        clearPendingWithdrawalIntent()
        await this.reload()
      }
    } finally {
      this.setData({ submitting: false })
    }
  },

  async onConfirmTransfer(event: WechatMiniprogram.TouchEvent) {
    const withdrawalNo = String(event.currentTarget.dataset.no)
    if (
      !withdrawalNo
      || this.data.confirmingWithdrawalNo
      || this.data.pollingWithdrawalNo
    ) return
    if (!wx.canIUse('requestMerchantTransfer')) {
      wx.showModal({
        title: '微信版本过低',
        content: '请更新微信后再确认收款。',
        showCancel: false,
      })
      return
    }
    this.setData({ confirmingWithdrawalNo: withdrawalNo })
    try {
      const confirmation = await merchantTransferConfirmation(withdrawalNo)
      const currentAppId = wx.getAccountInfoSync().miniProgram.appId
      if (confirmation.appId !== currentAppId) {
        throw new Error('当前小程序与提现单 AppID 不一致，已停止调起收款页')
      }
      const merchantWx = wx as MerchantTransferWx
      merchantWx.requestMerchantTransfer({
        mchId: confirmation.mchId,
        appId: confirmation.appId,
        package: confirmation.packageInfo,
        success: () => {
          this.setData({ confirmingWithdrawalNo: '' })
          wx.showToast({
            title: '正在核对微信转账结果',
            icon: 'none',
          })
          void this.pollWithdrawal(withdrawalNo)
        },
        fail: (result) => {
          this.setData({ confirmingWithdrawalNo: '' })
          if (result.errMsg.includes('cancel')) return
          wx.showToast({ title: '收款确认页调起失败', icon: 'none' })
        },
      })
    } catch (error) {
      this.setData({ confirmingWithdrawalNo: '' })
      wx.showToast({ title: errorText(error), icon: 'none' })
    }
  },

  async pollWithdrawal(withdrawalNo: string) {
    const generation = ++this.pollGeneration
    const startedAt = Date.now()
    let delayIndex = 0
    this.setData({ pollingWithdrawalNo: withdrawalNo })
    try {
      while (
        generation === this.pollGeneration
        && Date.now() - startedAt < MAX_POLL_ELAPSED_MS
      ) {
        const delay = POLL_DELAYS_MS[Math.min(
          delayIndex,
          POLL_DELAYS_MS.length - 1,
        )]
        delayIndex += 1
        await wait(delay)
        if (generation !== this.pollGeneration) return
        try {
          const detail = await myWithdrawal(withdrawalNo, false)
          if (generation !== this.pollGeneration) return
          this.setData({
            items: this.data.items.map((item) =>
              item.withdrawalNo === withdrawalNo ? listItem(detail) : item),
          })
          if (TERMINAL_STATUSES.has(detail.status)) {
            wx.showToast({
              title: detail.status === 'SUCCEEDED'
                ? '提现已成功'
                : '提现状态已更新',
              icon: detail.status === 'SUCCEEDED' ? 'success' : 'none',
            })
            await this.reload()
            return
          }
        } catch (error) {
          if (!(error instanceof MiniappApiProblem) || !error.retryable) {
            wx.showToast({ title: errorText(error), icon: 'none' })
            return
          }
        }
      }
      if (generation === this.pollGeneration) {
        wx.showToast({
          title: '转账仍在处理中，可稍后下拉刷新',
          icon: 'none',
        })
      }
    } finally {
      if (generation === this.pollGeneration) {
        this.setData({ pollingWithdrawalNo: '' })
      }
    }
  },

  requestPhoneBinding() {
    this.setData({ showPhoneGrant: true })
  },

  onPhoneGrantClose() {
    if (this.data.phoneGrantSubmitting) return
    this.setData({ showPhoneGrant: false })
    cancelAfterPhoneBindingPrompt()
  },

  onPhoneSheetPanelTap() {
    // 阻止点击面板内容时触发遮罩关闭。
  },

  async ensurePhoneBindingIntent() {
    if (!this.phoneBindingIntentKey) {
      this.phoneBindingIntentKey = await createIdempotencyKey()
    }
  },

  async onGetPhoneNumber(
    event: WechatMiniprogram.CustomEvent<WechatPhoneGrantDetail>,
  ) {
    if (this.data.phoneGrantSubmitting) return
    const code = event.detail.code
    if (!code) {
      if (isWechatPhoneGrantCancelled(event.detail)) {
        this.onPhoneGrantClose()
        return
      }
      reportWechatPhoneGrantError(event.detail)
      return
    }
    await this.ensurePhoneBindingIntent()
    this.setData({ phoneGrantSubmitting: true })
    try {
      await bindCurrentPhone(code, this.phoneBindingIntentKey)
      markPhoneBound()
      this.phoneBindingIntentKey = ''
      this.setData({ showPhoneGrant: false }, () => {
        if (!continueAfterPhoneBindingPrompt()) {
          wx.showToast({ title: '手机号已验证', icon: 'success' })
        }
      })
    } catch (error) {
      reportPhoneBindingError(error)
    } finally {
      this.setData({ phoneGrantSubmitting: false })
    }
  },

  onRetry() {
    void this.reload()
  },

  onContinueLoading() {
    void this.loadMore()
  },
})
