export function omitUndefinedRequestFields(
  data: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (data === undefined) return undefined

  // wx.request 会把 GET 参数中的 undefined 序列化成字面量
  // "undefined"。这里只删除顶层缺省字段；null、false、0 和空字符串
  // 仍属于调用方明确传入的数据，必须原样交给后端校验。
  return Object.fromEntries(
    Object.entries(data).filter(([, value]) => value !== undefined),
  )
}
