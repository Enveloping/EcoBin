/// <reference path="./types/index.d.ts" />

// 用 type 而非 interface：interface 不满足 wx 官方 typings 中 App<T extends IAnyObject> 约束
type IAppOption = {
  globalData: {
    /** JWT token */
    token?: string
    /** 当前用户角色：1-普通用户 2-清运员 3-设备管理员 */
    role?: number
    /** 登录返回的用户信息（含昵称/头像/tenantId 等） */
    userInfo?: import('../miniprogram/types/api').LoginResponse
  }
}
