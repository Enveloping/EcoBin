const CLEAN_ABORT_REASON_DESCRIPTION: Record<string, string> = {
  EDGE_RESTARTED:
    '香橙派在执行期间重启，系统已中止原操作；请按页面提示处理。',
  MCU_RESTART_FINAL_RESULT_UNAVAILABLE:
    '单片机重启且未取得最终结果包，本次清运未形成正常结果；请按页面提示处理。',
  MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE:
    '称重数据读取失败，本次清运失败；请检查称重模块后再处理。',
  MCU_WORK_CANCELLED:
    '单片机已取消本次清运；请根据结束原因和现场情况处理。',
  MCU_WORK_FAILED:
    '单片机报告本次清运执行失败；请根据结束原因检查设备。',
}

export function cleanAbortDescription(endReason: string | null): string {
  return endReason
    ? CLEAN_ABORT_REASON_DESCRIPTION[endReason]
      ?? '本次清运已中止，请根据结束原因和页面提示处理。'
    : '本次清运已中止，请按页面提示处理。'
}
