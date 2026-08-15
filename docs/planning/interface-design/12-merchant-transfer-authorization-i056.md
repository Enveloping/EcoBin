# EcoBin 微信免确认收款授权接口设计（I-056）

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-056 已确认**
>
> 确认日期：2026-08-06
>
> 2026-08-14 前向修订：明确未被微信受理的创建申请允许用户以新操作、新单号重新申请；
> 不确定结果仍锁住原单，禁止换号。
>
> 2026-08-15 前向修订：授权状态查询固定为普通查单，不申请“授权信息展示页”；
> 首次授权页始终使用创建授权时返回的原始 `packageInfo`。
>
> 说明：I-056 扩展 I-033/I-035。历史 `USER_CONFIRM` 提现继续使用原确认接口；所有新提现必须先完成一次微信官方授权，之后采用授权后自动收款。

## 1. 统一业务边界

1. “自动收款”只取消每笔提现的用户确认，不取消提现申请和工作人员审核，也不引入投递后自动提现。
2. 用户必须先在当前机构小程序中完成一次微信官方授权。前端页面调起成功不是授权成功；可信回调只负责触发原授权单查单，只有主动查询取得并核对完整 `TAKING_EFFECT` 证据后，才能创建新提现。
3. 授权属于当前机构用户、系统商户、AppID/OpenID 和转账场景。它不跨机构、不跨 AppID，也不能由工作人员代替用户确认。
4. 新提现创建时固化当前授权；审核、配置变化或任务重试不能替换授权。历史逐笔确认提现保持原模式直至终态。
5. 所有授权及资金响应使用 `Cache-Control: no-store`；OpenID、完整 `packageInfo`、通知明文和签名材料不进入日志、审计展示或普通 Web 响应。

## 2. 小程序授权资源

```http
GET  /api/v1/miniapp/me/merchant-transfer-authorization
POST /api/v1/miniapp/me/merchant-transfer-authorization-requests
POST /api/v1/miniapp/me/merchant-transfer-authorization/queries
Authorization: Bearer <aud=miniapp token>
```

### 2.1 查询当前授权

`GET` 只读取当前机构用户的本地授权投影，不在查询请求中偷偷调用微信。没有历史授权时返回：

```json
{
  "status": "NOT_OPENED",
  "authorizationNo": null,
  "appId": null,
  "mchId": null,
  "packageInfo": null,
  "confirmationRequired": false,
  "confirmationExpiresAt": null,
  "authorizedAt": null,
  "closedAt": null,
  "closeReason": null
}
```

公开状态固定为：

```text
NOT_OPENED
PREPARING
FAILED
WAIT_USER_CONFIRM
ACTIVE
CLOSED
EXPIRED
UNKNOWN
```

- `PREPARING` 表示本地请求和任务已经建立，但尚未取得可调起参数。
- `FAILED` 表示原创建任务收到能够证明“微信未受理”的明确永久错误并已停止。本地保存为
  `CREATE_REJECTED`，旧任务不可恢复外调，旧单号和全部证据继续保留，但不再占当前授权槽。
  用户排除原因后点击“重新申请”，服务端用新的幂等操作号和新的商户授权单号建立新请求。
  网络中断、响应验签失败或其他不确定结果必须进入 `UNKNOWN`，继续占槽并查原单，不能显示为
  `FAILED` 或换号。
- `WAIT_USER_CONFIRM` 才返回当前机构的 `appId/mchId/packageInfo` 和确认期限。
- `ACTIVE` 才允许创建新提现，不返回 `packageInfo`。
- `CLOSED` 允许用户重新申请，必须使用新的商户授权单号。
- `EXPIRED` 表示原待确认授权已超过 24 小时和微信 30 天记录保留期，且之后原单查单明确 `NOT_FOUND`；允许使用新单号重新申请。它不是普通网络 404 的别名。
- `UNKNOWN` 阻止重复申请和新提现，等待主动查单或运营对账；不能把未知状态显示成“未开通”。

### 2.2 创建授权申请

`POST` 使用 UUIDv4 `Idempotency-Key`，请求体为空对象：

```json
{}
```

服务端从可信小程序会话取得机构用户、AppID和OpenID，并验证：

- 用户、租户、机构和小程序当前有效；
- 用户已经绑定手机号；
- 系统普通商户启用且机构 AppID/商户绑定为 `VERIFIED`；
- 已获批的“二手回收”场景可用；
- 不存在 `PREPARING/WAIT_USER_CONFIRM/ACTIVE/UNKNOWN` 当前授权；历史 `FAILED` 不占当前槽，
  但同一个旧 `Idempotency-Key` 仍只回放旧失败申请，不会借回放创建新单。

成功短事务生成全平台唯一 `outAuthorizationNo`，冻结 AppID/OpenID/场景、用户展示名称、授权通知地址及请求摘要，并建立唯一创建授权任务。事务不调用微信，返回 `202 Accepted`：

- `userDisplayName` 由后端使用机构用户公开 UUID 确定性生成，只包含 ASCII 英文字母和数字，
  当前形状为 `JSBUser` 加 UUID 末 16 个十六进制字符；不接受客户端昵称，避免标点、表情、
  控制字符、超长或重试时变化。
- P0 默认不传可空 `userRecvPerception` 和 `sceneInfo`；若未来按获批场景启用，必须先版本化规则并纳入同一请求摘要，不能在原授权单重试时改变。
- `authorizationNotifyUrl` 固定为真实模式通知根地址下的 `/api/v1/wechat-pay/notifications/merchant-transfer-authorizations`，必须是公网 HTTPS、无查询参数和片段；完整 URL 与 SHA-256 摘要都在创建时冻结。

```json
{
  "authorizationNo": "AU20260806...",
  "status": "PREPARING",
  "statusUrl": "/api/v1/miniapp/me/merchant-transfer-authorization",
  "recommendedPollAfterMs": 1000
}
```

相同幂等键和相同请求返回原申请；不同请求冲突。已有 `ACTIVE` 授权时直接返回当前授权，不生成第二条。已有待确认申请时返回原申请及当前状态，不消耗微信允许的授权数量。已有历史 `FAILED` 时，只有新的幂等键才会生成新的商户授权单号；原键永远回放原失败记录。

### 2.3 主动刷新授权

`POST /merchant-transfer-authorization/queries` 使用 UUIDv4 `Idempotency-Key` 和空对象请求体。它只唤醒当前授权对应的唯一 `QUERY_MERCHANT_TRANSFER_AUTHORIZATION` 任务，不按操作号创建并行查单任务，也不创建新授权；返回 `202 + statusUrl`。当前没有授权时返回 `404 RESOURCE.NOT_FOUND`，已经 `CLOSED/EXPIRED` 时直接返回当前投影。

小程序准备调起授权页前必须先请求一次主动刷新并轮询 `GET`。只有刷新后的状态仍为 `WAIT_USER_CONFIRM`、当前时间早于 `confirmationExpiresAt` 且 `packageInfo` 非空时，才允许调起；这落实微信“调起前先查单”的建议，也避免使用已经过期或刚刚生效/关闭的页面参数。

后端向微信执行这次状态查询时必须省略 `is_display_authorization`（即采用默认
`false`）。`is_display_authorization=true` 只用于已经生效的授权生成“授权信息展示页”参数，
不能用于待用户确认阶段的首次授权页。首次授权页所需 `packageInfo` 归创建接口响应所有：
状态查询在 `WAIT_USER_CONFIRM` 时可以不返回 `packageInfo`，后端必须保留原创建响应中的值，
不得用查询响应替换或清空它。

## 3. 小程序调起授权

当查询返回 `WAIT_USER_CONFIRM` 时，小程序必须：

1. 校验返回的 `appId` 等于 `wx.getAccountInfoSync().miniProgram.appId`；
2. 检查客户端是否支持 `wx.requestMerchantTransfer`；
3. 使用 `mchId + appId + packageInfo` 调起微信官方免确认收款授权页；
4. 页面返回后继续轮询 EcoBin `GET`，不能在本地把授权设为 `ACTIVE`。

`requestMerchantTransfer:ok` 只表示授权页面展示并返回应用，不证明用户已经授权。用户取消保持 `WAIT_USER_CONFIRM`；超过微信 24 小时确认期限后，后端必须先查原授权单。可信 `CLOSED` 可立即重新申请；若服务离线跨过保留期，则只有同时满足“可信创建事实、期限已超过 30 天、从未生效、查单明确 `NOT_FOUND`”才能进入 `EXPIRED` 后重新申请，其他 404 或临时失败进入 `UNKNOWN` 并阻止新单。

## 4. 授权通知与主动查询

```http
POST /api/v1/wechat-pay/notifications/merchant-transfer-authorizations
```

通知入口沿用 APIv3 验签、解密和可靠 inbox 规则。只有通知验证完成且唯一 inbox/处理任务已提交，才能向微信确认收件。通知无法到达时，唯一查询授权任务按原 `outAuthorizationNo` 主动查询兜底；临时错误采用分级退避，不能跟随 1 秒 worker 热轮询。

普通查单成功返回 `WAIT_USER_CONFIRM` 时，系统记录本次成功查询时间、清除旧查询错误并继续
保留创建阶段的 `packageInfo`。若固定查单请求被微信以 `INVALID_REQUEST` 拒绝，重复发送同一
请求不会改变结果，因此当前查询任务进入阻塞并等待排障后人工恢复，不允许后台无限重试。

回调和查询共用同一归并器。每种来源必须核对微信实际提供的全部身份字段；查单需逐项核对：

```text
outAuthorizationNo
authorizationId
appId
openId
transferSceneId
userDisplayName
userRecvPerception
state and channel times
```

授权通知本身不返回场景和用户收款感知，因此以已验签的 `outAuthorizationNo + authorizationId + appId + openId + userDisplayName` 核对原请求；若任一字段缺失或矛盾，只保存观察、推进本地 `UNKNOWN` 并建立对账异常，不得启用提现。系统仍须以主动查单补齐完整授权证据，不能只依赖通知。

## 5. 新提现强制授权

I-033 的创建接口路径不变：

```http
POST /api/v1/miniapp/me/withdrawals
```

新增前置条件：当前授权必须是 `ACTIVE`，并且授权的商户、绑定、AppID、OpenID和场景仍与当前提现身份一致。成功创建时返回：

```json
{
  "withdrawalNo": "WD20260806...",
  "amountYuan": "10.00",
  "status": "PENDING_REVIEW",
  "collectionMode": "AUTHORIZED",
  "confirmationRequired": false,
  "createdAt": "2026-08-06T10:20:00.123Z"
}
```

未开通、待确认或已关闭时返回：

```text
422 WITHDRAWAL.AUTO_COLLECTION_AUTHORIZATION_REQUIRED
```

授权状态未知时返回：

```text
409 WITHDRAWAL.AUTO_COLLECTION_AUTHORIZATION_UNRESOLVED
```

任一拒绝都不创建提现单、不冻结用户余额或机构额度，也不占用提现幂等成功槽。

## 6. 审核后自动收款

审核仍只决定是否允许按原金额继续。提交 worker 在外调前重新核对租户、机构、用户、商户绑定和本提现固化的授权仍然有效：

- 有效时调用普通商户 APIv3“用户授权后转账”；
- 已关闭、过期、未知或不再匹配且尚无微信转账单时，不调用微信；系统原子推进 `LOCAL_ABORTED_BEFORE_CHANNEL`、释放双方冻结并删除活动槽，并在订单记录中保留安全原因；
- 用户重新授权后创建新的提现。原提现不能替换授权、重新进入待提交或复用原幂等成功结果；用户仍不能主动取消提现；
- 一旦微信转账单已建立，后续只能使用原 `outBillNo` 查单或原参数续办，不能更换授权、转账单号或收款人。

授权后转账不返回逐笔确认 `packageInfo`。新 `AUTHORIZED` 提现详情始终 `confirmationRequired=false`，小程序不显示“确认微信收款”。旧 `USER_CONFIRM` 在途单仍由 I-035 原确认接口提供按钮。

## 7. 真实验收

至少使用指定小额账号验证：

1. 用户首次授权，主动查询确认 `TAKING_EFFECT`；回调到达时能可靠触发同一原授权单查单；
2. 创建提现、人工审核通过后无需再次点击，微信零钱真实到账；
3. 微信 `SUCCESS` 证据、提现终态、用户钱包和机构额度一致；
4. 用户在微信关闭授权后，新提现被创建前拒绝；审核后关闭的竞态不会调用错误授权；
5. 历史逐笔确认提现仍可完成，不被批量改成授权模式；
6. 回调缺失、重复、乱序、超时和重启不会重复授权、重复出款或释放冻结。

## 8. 当前实施状态

I-056 对应的 Controller、OpenAPI、渠道端口、可靠任务、通知入口、提现状态机、Web 展示和小程序授权流程已经在代码中实现；V51 又补齐明确创建拒绝后的安全换单流程。Fake 模式及真实 MySQL 约束路径由自动化验证覆盖。

当前仍属于“代码完成、真实渠道待验收”：部署新应用前必须先迁移到 V35；之后还需使用已开通用户授权免确认模式的真实普通商户，完成首次授权、授权后小额转账、主动查单及通知回调验收。域名回调暂不可达时可以先验证主动查询链路，但在授权与转账回调均验收前，不能视为完整上线。

## 9. 官方规则依据

- [发起免确认收款授权](https://pay.weixin.qq.com/doc/v3/merchant/4015901167)：授权申请 24 小时有效，待确认记录保留 30 天，同一用户在当前商户场景下的待确认与生效授权合计最多 5 个。
- [商户单号查询授权结果](https://pay.weixin.qq.com/doc/v3/merchant/4014399423)：返回 AppID、OpenID、授权双单号、场景、展示名称、收款感知及 `WAIT_USER_CONFIRM/TAKING_EFFECT/CLOSED`。
- [免确认收款授权结果通知](https://pay.weixin.qq.com/doc/v3/merchant/4014512908)：确认/关闭事件需验签解密、幂等处理并用主动查询兜底。
- [用户授权后转账](https://pay.weixin.qq.com/doc/v3/merchant/4014399371)：`authorization_id/out_authorization_no` 二选一，结果不明确时不得立即更换 `out_bill_no`。
