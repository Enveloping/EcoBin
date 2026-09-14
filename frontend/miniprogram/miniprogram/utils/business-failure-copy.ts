const DELIVERY_END_REASON_TEXT: Record<string, string> = {
  EDGE_RESTARTED: '香橙派重启，本次投递已取消，请按提示处理',
  MCU_RESTART_FINAL_RESULT_UNAVAILABLE:
    '单片机重启，未取得最终结果包，本次投递失败，请联系客服处理',
  MCU_DELIVERY_FINAL_WEIGHT_UNAVAILABLE:
    '称重数据读取失败，本次投递失败，请按提示处理',
  MCU_WORK_CANCELLED: '设备已取消本次投递，请按提示处理',
  MCU_WORK_FAILED: '设备执行本次投递失败，请按提示处理',
  REMOTE_RECOVERY_QUARANTINED:
    '本次投递已由远程维护中止，请联系客服处理',
}

export function deliveryAbortedMessage(endReason: string | null): string {
  return endReason
    ? DELIVERY_END_REASON_TEXT[endReason]
      ?? '本次投递已中止，请按提示处理'
    : '本次投递已中止，请按提示处理'
}

export const CLEAN_ABORTED_COPY = {
  title: '本次清运已中止',
  description: '本次清运未能完成，请按页面提示处理；需要时联系管理员排查。',
} as const
