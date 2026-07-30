/// <reference path="./types/index.d.ts" />

// 用 type 而非 interface：interface 不满足 wx 官方 typings 中 App<T extends IAnyObject> 约束
type IAppOption = {
  globalData: {
    /** 单一 audience 会话；不保存 Refresh Token 或第二套模式 Token。 */
    session?: import('../miniprogram/types/api').LoginResponse
    /** 仅开发环境使用的界面投影；不代表服务端身份或权限。 */
    testViewMode?: import('../miniprogram/types/api').EntryMode
  }
}
