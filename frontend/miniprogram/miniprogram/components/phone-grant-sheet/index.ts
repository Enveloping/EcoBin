import { bindCurrentPhone } from '../../api/auth'
import { markPhoneBound } from '../../utils/auth'
import { createIdempotencyKey } from '../../utils/command-intent'

type PhoneGrantData = {
  submitting: boolean
  bindingIntentKey: string
}

type PhoneGrantProperties = {
  [key: string]: WechatMiniprogram.Component.AllProperty
  visible: {
    type: BooleanConstructor
    value: boolean
    observer: string
  }
}

type PhoneGrantMethods = {
  [key: string]: (...args: any[]) => any
  onVisibleChange(visible: boolean): void
  ensureBindingIntent(): Promise<void>
  onPanelTap(): void
  onClose(): void
  onGetPhoneNumber(
    event: WechatMiniprogram.CustomEvent<{
      code?: string
      errMsg?: string
    }>,
  ): Promise<void>
}

type PhoneGrantInstance = WechatMiniprogram.Component.Instance<
  PhoneGrantData,
  PhoneGrantProperties,
  PhoneGrantMethods
>

Component<PhoneGrantData, PhoneGrantProperties, PhoneGrantMethods>({
  properties: {
    visible: {
      type: Boolean,
      value: false,
      observer: 'onVisibleChange',
    },
  },

  data: {
    submitting: false,
    bindingIntentKey: '',
  },

  methods: {
    onVisibleChange(this: PhoneGrantInstance, visible: boolean) {
      if (visible) void this.ensureBindingIntent()
    },

    async ensureBindingIntent(this: PhoneGrantInstance) {
      if (!this.data.bindingIntentKey) {
        this.setData({
          bindingIntentKey: await createIdempotencyKey(),
        })
      }
    },

    onPanelTap(this: PhoneGrantInstance) {
      // 阻止点击面板内容时触发遮罩关闭。
    },

    onClose(this: PhoneGrantInstance) {
      if (!this.data.submitting) this.triggerEvent('close')
    },

    async onGetPhoneNumber(
      this: PhoneGrantInstance,
      event: WechatMiniprogram.CustomEvent<{
        code?: string
        errMsg?: string
      }>,
    ) {
      if (this.data.submitting) return
      const code = event.detail.code
      if (!code) {
        wx.showToast({ title: '已取消手机号授权', icon: 'none' })
        return
      }

      await this.ensureBindingIntent()
      this.setData({ submitting: true })
      try {
        const binding = await bindCurrentPhone(
          code,
          this.data.bindingIntentKey,
        )
        markPhoneBound()
        wx.showToast({ title: '手机号已验证', icon: 'success' })
        this.triggerEvent('bound', {
          maskedPhoneNumber: binding.maskedPhoneNumber,
        })
      } finally {
        this.setData({ submitting: false })
      }
    },
  },
})
