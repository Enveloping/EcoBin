# EcoBin P0 目标接口设计：钱包调整、充值、提现与微信渠道（I-031～I-035）

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-031～I-035 已确认**
>
> 说明：本文件定义人工钱包调整、机构出款账户、Native 充值、用户手动提现、后台审核/客服处置、微信商家转账和公共出款闸门的 P0 契约。P0 只接入普通商户 APIv3 的用户确认收款模式；投递后自动提现和免确认收款授权继续留到 M1。

## 本章统一边界

1. 用户钱包、机构出款账户、EcoBin 提现单、微信支付单和微信转账单是不同资源。客户端响应必须分别表达本地业务状态、微信原始渠道状态和技术任务状态，不能把“已受理”“任务已创建”“待用户确认”说成“已经到账”。
2. 金额继续遵守 I-002：Web/小程序使用两位小数人民币元字符串，微信连接器边界使用整数分；禁止 JSON 浮点数。所有金额、配置和收款身份在业务单创建时固化，后续配置变化不追溯旧单。
3. 所有资金写操作使用 UUIDv4 `Idempotency-Key`。同键同摘要返回原结果，同键异摘要冲突；受理前拒绝不建立失败业务单，也不永久占用成功幂等槽。
4. 所有外部微信调用都发生在本地短事务提交之后。事务先固定业务身份、外部商户单号、完整请求快照和唯一可靠任务；HTTP 超时、进程崩溃或未知应答后只允许用原单号和原参数查单或续办。
5. 微信回调不使用 Web Cookie、小程序 Token、CSRF 或普通 `Result<T>` 包装。入口只按 APIv3 签名、可信验签材料、通知密文和唯一 inbox 鉴别；只有验签/解密完成且唯一 inbox 与 `PROCESS_INBOX` 可靠任务共同提交后才按微信协议确认收件，可靠落库失败必须返回失败。领域归并在收件后异步重试，不能把“尚未完成领域处理”误写成业务成功。
6. 机构额度是 EcoBin 本地账本额度，不等于公司微信运营账户余额。机构之间不调拨、不共享账本；全机构只共享系统商户的外部流动性和平台出款闸门。
7. P0 禁止充值退款、人工修改机构额度、修改提现金额、替换提现收款人、后台直接填写微信终态、换号重试和删除资金证据。平台管理员也只能执行本章明确列出的镜像业务用例。
8. 资金与渠道响应统一设置 `Cache-Control: no-store`。AppSecret、APIv3 密钥、商户私钥、签名原文、通知明文、OpenID 和完整 `package_info/codeUrl` 不进入应用日志或操作审计展示字段。

### 路径基准

普通租户和平台协助继续使用两套显式 Web 根：

```text
普通租户：/api/v1/web/organizations/{organizationCode}
平台协助：/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}
```

后文 `{organizationBase}` 表示上述两种路径分别展开。平台管理员不能调用普通租户路径，普通工作人员不能在请求体自报租户或机构。

微信通知使用不带租户参数的系统入口：

```text
POST /api/v1/wechat-pay/notifications/native-payments
POST /api/v1/wechat-pay/notifications/merchant-transfers
```

通知中的商户单号只能定位已经存在且带不可变作用域的渠道单，不能由通知体创建、选择或改挂机构。

### 权限目录

本章新增以下稳定能力码：

| 能力码 | 作用域 | 能力边界 |
|---|---|---|
| `wallet.adjust` | `TENANT/ORGANIZATION` | 对授权机构用户钱包执行有明细的人工差额调整；不包含查询全部钱包或机构资金 |
| `fund.read` | `TENANT/ORGANIZATION` | 查询机构出款账户、充值、机构资金明细和资金汇总；不包含用户钱包明细 |
| `recharge.create` | `TENANT/ORGANIZATION` | 为授权机构创建需要付款人主动扫码确认的 Native 充值单；不包含人工入账或退款 |
| `withdrawal.configuration.manage` | `TENANT/ORGANIZATION` | 发布授权机构新的手动提现配置版本 |
| `withdrawal.read` | `TENANT/ORGANIZATION` | 查询授权范围内提现单及安全渠道摘要 |
| `withdrawal.handle` | `TENANT/ORGANIZATION` | 对直接目标执行渠道前终止、原单查单或微信撤销请求；不包含审核、修改金额或伪造终态 |

- 提现初审继续复用 `review.execute`；它只补足当前可审核 `PENDING_REVIEW` 直接目标，不开放全部提现历史。
- `wallet.read`、`wallet.adjust`、`fund.read`、`recharge.create`、`withdrawal.configuration.manage`、`withdrawal.read`、`withdrawal.handle` 和 `review.execute` 互不隐含。
- 只有 `wallet.adjust` 而没有 `wallet.read/user.read` 时，操作者可以读取完成本次调整所需的直接目标余额、版本和停投闸安全摘要，但不能遍历钱包、查找用户或读取历史流水。
- 租户主体账号、租户总部和机构负责人按既有天然能力规则取得相应作用域能力。平台管理员使用镜像路径并记录目标租户、机构、操作者和可空原因。
- 公共出款闸门恢复只属于平台域受控用例，不定义成可下放给租户或机构的权限码。

### 版本和稳定身份

| 资源 | 公开身份 | 并发版本 |
|---|---|---|
| 钱包调整 | `adjustmentUid` | 请求携带目标 `expectedWalletVersion` |
| 提现配置 | 机构路径 + `versionNo` | 发布请求携带 `expectedCurrentVersion` |
| 充值单 | `rechargeNo` | 查询返回 `version`；普通客户端不覆盖渠道状态 |
| 提现单 | `withdrawalNo` | 审核、取消和处置携带 `expectedVersion` |
| 平台出款闸门 | 系统商户固定资源 | 恢复携带 `expectedGateVersion` 和当前暂停事件 UID |

微信 `out_trade_no`、微信支付单号、`out_bill_no` 和 `transfer_bill_no` 是渠道关联身份，不替代 EcoBin 公开资源号。列表和详情不得暴露内部 `BIGINT` 主键。

## I-031 钱包人工调整

**已确认：人工调整只接受有符号差额，系统在同一事务生成调整事实、钱包明细和余额投影；任何主体都不能无明细覆盖目标余额。**

### 1. 接口

普通 Web 与平台镜像：

```http
POST {organizationBase}/organization-users/{organizationUserUid}/wallet-adjustments
GET  {organizationBase}/wallet-adjustments?organizationUserUid={optional}&occurredFrom={optional}&occurredTo={optional}&cursor={opaque}&limit={1..100}
GET  {organizationBase}/wallet-adjustments/{adjustmentUid}
```

调整请求：

```json
{
  "deltaYuan": "-10.00",
  "expectedWalletVersion": 12,
  "reason": null
}
```

- `deltaYuan` 必须精确到分且非 0；正数增加可提现余额，负数减少。请求不接受目标余额、提现冻结金额、钱包状态、停投阈值、提现单号或操作者身份。
- P0 不另设人工调整业务金额上限，但必须可无损转换为整数分，且本次前后余额和明细代数均能安全落入目标有符号整数范围。Web 在提交前必须明确展示调整前余额、差额和预计调整后余额并要求操作者确认；该 UI 确认不是第二人审批或新的服务端状态。
- 用户查找继续要求 `user.read`；已知 `organizationUserUid` 时，`wallet.adjust` 可以读取直接目标的安全钱包投影和 `walletVersion`，但不能借此查询用户列表或钱包流水。
- 调整列表要求 `wallet.read`；详情允许 `wallet.read`，或仅为核对直接操作结果而持有目标机构 `wallet.adjust`。两者都不返回 OpenID、完整手机号或内部钱包主键。

### 2. 原子结果

外层协调器先锁当前机构投递配置 head，取得本次资金变动线性化时的负余额停投阈值，再按资金锁序：

1. 复核目标用户、机构、钱包、操作者作用域、实时能力和 `expectedWalletVersion`。
2. 在钱包锁内计算调整前后余额；创建唯一 `fund_wallet_adjustment` 和 `MANUAL_ADJUSTMENT` 钱包明细，分配钱包内及机构可见序号并更新钱包投影。
3. 调整后余额达到或低于当前阈值时，锁存 `MANUAL_RECOVERY_REQUIRED` 和本次阈值快照。已经锁存时，只有本次非零人工正向调整使余额严格高于锁存阈值，才在同一事务恢复 `OPEN`；普通返现和阈值变化不能代替人工恢复。
4. 调整后实际余额 `< 0` 时，尚未越过微信调用边界的活动提现增加负余额暂停并保持双方冻结；已经越界的只增加风险标记。恢复到 `>= 0` 时自动解除渠道前暂停并唤醒原单，不创建新提现。
5. 写成功审计。原因可以为空，但调整前后金额、操作者和发生时间始终保存。

任一步失败整体回滚，不得出现调整事实、钱包明细、余额、停投闸和活动提现只更新一部分。

成功返回 `201 Created`：

```json
{
  "adjustmentUid": "717179cf-...",
  "entryUid": "b8ee9931-...",
  "deltaYuan": "-10.00",
  "availableBalanceBeforeYuan": "8.50",
  "availableBalanceAfterYuan": "-1.50",
  "walletVersion": 13,
  "deliveryGate": "OPEN",
  "activeWithdrawalEffect": "PAUSED_BEFORE_CHANNEL",
  "occurredAt": "2026-07-23T10:00:00.123Z"
}
```

`activeWithdrawalEffect` 固定为 `NONE/PAUSED_BEFORE_CHANNEL/RISK_MARKED_AFTER_CHANNEL/RESUMED_BEFORE_CHANNEL`。它只说明本次调整对既有提现的联动，不是新的提现终态。

主要错误包括：

```text
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
409 WALLET.VERSION_CONFLICT
409 COMMON.IDEMPOTENCY_KEY_CONFLICT
422 WALLET.ADJUSTMENT_OUT_OF_STORAGE_RANGE
400 COMMON.VALIDATION_FAILED
```

## I-032 机构资金查询与 Native 充值

**已确认：Web 只提交充值毛额，后端先持久化本地充值单和唯一 Native 下单意图，再异步取得二维码；只有可信微信支付成功事实才能触发唯一净额入账。**

### 1. 机构出款账户和资金明细

普通 Web 与平台镜像：

```http
GET {organizationBase}/payout-account
GET {organizationBase}/payout-account/entries?entryType={optional}&sourceNo={optional}&occurredFrom={optional}&occurredTo={optional}&cursor={opaque}&limit={1..100}
GET {organizationBase}/payout-account/entries.csv?entryType={optional}&occurredFrom={required}&occurredTo={required}
```

全部端点要求 `fund.read`。账户详情在一个一致读取中返回：

```json
{
  "availablePayoutYuan": "900.00",
  "frozenWithdrawalYuan": "100.00",
  "totalPayoutYuan": "1000.00",
  "cumulativeRechargeGrossYuan": "1010.11",
  "cumulativeRechargeFeeYuan": "6.07",
  "cumulativeRechargeNetYuan": "1004.04",
  "cumulativeSuccessfulWithdrawalYuan": "4.04",
  "merchantBindingStatus": "VERIFIED",
  "payoutGateStatus": "OPEN",
  "asOf": "2026-07-23T10:05:00.123Z"
}
```

- `totalPayoutYuan = availablePayoutYuan + frozenWithdrawalYuan`。累计值从不可变明细或其可证明只读投影汇总，不能在账户行维护第二套真相。
- 机构明细类型至少为 `RECHARGE_POSTED/WITHDRAWAL_FREEZE/WITHDRAWAL_SUCCEEDED/WITHDRAWAL_RELEASED`，逐项返回可用/冻结增量、前后值、来源单号和时间。
- 列表首屏冻结内部提交水位并封入防篡改游标；客户端看不到内部 ID。CSV 使用相同作用域、筛选和快照口径，不得绕过 `fund.read`、导出其他机构或输出 OpenID/密钥/通知原文。
- 普通机构响应只表明平台闸门是否暂停，不返回公司运营账户余额、触发提现的其他机构或内部告警详情。

### 2. 充值接口

```http
POST {organizationBase}/recharge-orders
GET  {organizationBase}/recharge-orders?status={optional}&createdFrom={optional}&createdTo={optional}&cursor={opaque}&limit={1..100}
GET  {organizationBase}/recharge-orders/{rechargeNo}
```

- 创建需要 `recharge.create`；列表和详情需要 `fund.read`。`recharge.create` 单独只允许读取本次新单及其二维码准备状态，不开放历史列表。
- 同一机构允许多笔未支付充值并存。目标机构来自路径和受信上下文；请求不接受付款人、AppID、商户号、费率、手续费、净额、到期时间或渠道单号。
- 列表首屏冻结充值创建水位，按 `createdAt + rechargeNo` 稳定倒序；支付状态、二维码准备状态和待入账状态按每页当前值重算，刷新首屏取得完整当前队列。

请求：

```json
{
  "grossAmountYuan": "1000.00"
}
```

创建事务必须：

1. 锁定并校验机构、操作者、`recharge.create`、机构小程序和系统商户绑定均有效，金额为 `1.00..200000.00` 元。
2. 按 `feeCent=ceil(grossCent*6000/1000000)` 计算并固化 0.6% 手续费及净额；全程使用整数运算。
3. 分配唯一 `rechargeNo` 和系统商户内唯一 `outTradeNo`，创建充值单、微信支付请求快照、30 分钟 `expiresAt/time_expire`、唯一 Native 下单可靠任务和审计。

事务不调用微信。成功返回 `202 Accepted`，`Location` 指向充值详情：

```json
{
  "operationId": "6dc95c4b-...",
  "resourceId": "RC20260723...",
  "rechargeNo": "RC20260723...",
  "status": "PENDING_PAYMENT",
  "version": 0,
  "grossAmountYuan": "1000.00",
  "feeYuan": "6.00",
  "netAmountYuan": "994.00",
  "paymentPreparationStatus": "PENDING",
  "qrCodeUrl": null,
  "expiresAt": "2026-07-23T10:35:00.123Z",
  "statusUrl": "/api/v1/web/organizations/org-a/recharge-orders/RC20260723...",
  "recommendedPollAfterMs": 1000
}
```

`paymentPreparationStatus` 固定为 `PENDING/RETRYING/READY/FAILED`：

- `READY` 且充值仍为 `PENDING_PAYMENT`、尚未到期时才返回完整 `qrCodeUrl`；Web 用该 URL 生成二维码，不由后端拼二维码图片。
- 网络超时或结果不明进入 `RETRYING`，执行器只使用原 `outTradeNo` 和原请求续办；不得创建第二微信支付单。
- 微信明确拒绝且能够证明没有支付单成立时进入 `FAILED` 并关闭本地单；错误安全摘要可展示给有权限人员，签名、请求原文和凭证不得返回。
- 支付成功回调或查单可以先于 Native 创建响应到达。可信成功事实一旦成立，充值进入 `PAID_PENDING_POST/POSTED`，任何迟到 `code_url`、创建错误或重试结果都只能追加观察，不能把业务状态退回待支付或再向前端开放二维码。`FAILED` 只允许与尚无成功事实的本地 `CLOSED` 对应。

充值业务状态固定为：

```text
PENDING_PAYMENT
PAID_PENDING_POST
POSTED
CLOSED
EXPIRED
```

### 3. 支付成功、查单与到期

- Native 支付通知进入 `/api/v1/wechat-pay/notifications/native-payments`。服务端先验证 APIv3 签名并解密，在收件短事务中共同保存唯一 inbox 和唯一 `PROCESS_INBOX` 可靠任务；事务提交后即可按微信协议确认收件。无效、无法解密或无法定位已有支付单的通知不能修改业务数据；可靠收件失败也不能确认成功。
- 回调、主动查单和以后对账归并同一微信支付观察。可信成功必须核对系统商户、机构 AppID、`outTradeNo`、总金额和币种；二维码实际付款人的 OpenID 只作渠道证据，不能决定充值机构。
- 收件后的第一个领域短事务追加成功观察，把充值推进 `PAID_PENDING_POST` 并建立唯一净额入账任务；处理失败由原 `PROCESS_INBOX` 任务重试，不要求微信再次投递同一通知。
- 第二个事务锁充值和机构账户，追加唯一 `RECHARGE_POSTED` 明细、增加净额并推进 `POSTED`。失败保持 `PAID_PENDING_POST`，原任务继续补齐，重复通知最多入账一次。
- 30 分钟到期时先用原单号查单。已成功按成功处理；仍未支付时请求微信关单，只有关单受理并最终查得关闭事实后才进入 `EXPIRED`。本地计时、二维码失效或用户页面关闭都不能单独证明渠道关闭。
- EcoBin 不提供退款端点。渠道以后出现退款、金额矛盾或相反终态时，只追加观察并建立对账异常，不自动冲减机构额度或改挂机构。

主要错误包括：

```text
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
409 COMMON.IDEMPOTENCY_KEY_CONFLICT
422 FUNDS.RECHARGE_AMOUNT_OUT_OF_RANGE
422 FUNDS.MINIAPP_MERCHANT_BINDING_UNAVAILABLE
422 FUNDS.RECHARGE_CHANNEL_UNAVAILABLE
503 COMMON.RETRY_EXHAUSTED
```

## I-033 提现配置与用户手动提现

**已确认：P0 只开放手动提现且全部人工审核；用户创建提现时，提现单、唯一活动占位、用户资金冻结和机构额度冻结在一个事务共同成立。**

### 1. 手动提现配置

普通 Web 与平台镜像：

```http
GET  {organizationBase}/withdrawal-configuration
GET  {organizationBase}/withdrawal-configuration-versions?cursor={opaque}&limit={1..100}
POST {organizationBase}/withdrawal-configuration-releases
```

- 当前配置和版本历史读取需要 `withdrawal.configuration.manage` 或 `withdrawal.read`；发布需要 `withdrawal.configuration.manage`。
- 发布请求创建新的不可变版本并原子切换当前 head，不更新旧版本。请求只包含：

```json
{
  "expectedCurrentVersion": 3,
  "hardLimitYuan": "200.00",
  "manualMinimumYuan": "0.10",
  "manualMaximumYuan": "200.00"
}
```

- 约束为 `0.10 <= manualMinimum <= manualMaximum <= hardLimit <= 200.00`。
- P0 的 `manualReviewFreeThresholdYuan` 固定返回 `"0.00"`，发布请求不接收该字段；自动提现开关、范围和阈值也不进入 DTO。以后开放必须新版本演进接口与数据库，不能让客户端提前提交隐藏字段。
- 新机构默认 `hardLimit=200.00/manualMinimum=0.10/manualMaximum=200.00/reviewFreeThreshold=0.00`。配置只影响发布后创建的提现，旧提现保留原快照。

### 2. 用户接口

```http
POST /api/v1/miniapp/me/withdrawals
GET  /api/v1/miniapp/me/withdrawals?status={optional}&cursor={opaque}&limit={1..100}
GET  /api/v1/miniapp/me/withdrawals/{withdrawalNo}
POST /api/v1/miniapp/me/withdrawals/{withdrawalNo}/cancellations
Authorization: Bearer <aud=miniapp token>
```

创建请求：

```json
{
  "amountYuan": "10.00"
}
```

请求不接受机构、用户、AppID、OpenID、配置、审核状态、机构额度或商户信息。服务端从当前小程序会话取得全部收款作用域并在固定资金锁序中检查：

1. 当前机构用户有效且已绑定手机号；钱包可提现余额 `>= 0`、金额充足并满足当前配置。
2. 同钱包没有活动提现；机构可用出款额度充足。
3. 机构 AppID 与系统商户绑定有效；平台出款闸门为 `OPEN`。
4. P0 不收集 `userName`。若提交前已经确定渠道要求姓名而系统无法提供，直接拒绝并提示联系客服。

任一前置条件不成立时不创建提现、失败记录、活动槽、任务或任何资金明细。成功事务共同创建提现、活动槽和双方 `FREEZE` 明细，把用户可用转为提现处理中、机构可用转为冻结，并返回 `201 Created`：

```json
{
  "withdrawalNo": "WD20260723...",
  "amountYuan": "10.00",
  "status": "PENDING_REVIEW",
  "version": 0,
  "reviewRequired": true,
  "channelState": null,
  "longUnsettled": false,
  "canCancel": true,
  "canConfirmReceipt": false,
  "createdAt": "2026-07-23T10:20:00.123Z"
}
```

提现业务状态是闭集：

```text
PENDING_REVIEW
READY_TO_SUBMIT
CHANNEL_PROCESSING
SUCCEEDED
REJECTED
LOCAL_CANCELLED
LOCAL_ABORTED_BEFORE_CHANNEL
CHANNEL_FAILED
CHANNEL_CANCELLED
```

`negativeBalancePaused`、`postSubmissionRisk` 和 `longUnsettled` 是正交标记，不得为了表达暂停、风险或超时另造会覆盖主生命周期的状态。

- 用户列表按 `createdAt + withdrawalNo` 稳定倒序；详情只返回本人、当前机构的金额、阶段时间、安全失败原因和下一动作，不返回审核人身份、OpenID、内部渠道请求或后台审计。
- P0 所有新单固定进入 `PENDING_REVIEW`。以后即使配置模型开放免审，也必须由新的接口版本显式改变，不能让当前客户端猜阈值。
- 取消请求携带 `expectedVersion` 和可空原因，只允许本人对 `PENDING_REVIEW` 且不存在微信转账单的手动提现执行。成功事务推进 `LOCAL_CANCELLED`、释放双方冻结、追加双方 `FINAL` 明细并删除活动槽；返回 `200`。
- 已审核通过、已存在微信转账单或已终态时不能本地取消。同一幂等键重放原取消结果，其他竞争返回版本/状态冲突。

主要创建拒绝码包括：

```text
422 WITHDRAWAL.PHONE_NOT_BOUND
422 WITHDRAWAL.AMOUNT_OUT_OF_RANGE
422 WITHDRAWAL.WALLET_BALANCE_NEGATIVE
422 WITHDRAWAL.WALLET_BALANCE_INSUFFICIENT
409 WITHDRAWAL.ALREADY_ACTIVE
422 WITHDRAWAL.ORGANIZATION_FUNDS_INSUFFICIENT
503 WITHDRAWAL.PAYOUT_TEMPORARILY_PAUSED
422 WITHDRAWAL.REAL_NAME_REQUIRED
422 FUNDS.MINIAPP_MERCHANT_BINDING_UNAVAILABLE
```

## I-034 提现查询、审核与客服处置

**已确认：审核只决定是否允许按原金额继续；客服只能在渠道边界内终止、查单或请求撤销，不能修改提现或伪造微信终态。**

### 1. Web 查询

普通 Web 与平台镜像：

```http
GET {organizationBase}/withdrawals?status={optional}&organizationUserUid={optional}&longUnsettled={optional}&createdFrom={optional}&createdTo={optional}&cursor={opaque}&limit={1..100}
GET {organizationBase}/withdrawals/{withdrawalNo}
```

- 全量列表需要 `withdrawal.read`。只有 `review.execute` 时，列表固定为当前可审核的 `PENDING_REVIEW` 队列，详情也只开放该直接目标。
- `withdrawal.handle` 单独只允许读取执行处置所需的直接目标状态、版本和安全渠道摘要，不开放历史列表。
- 列表首屏冻结创建水位，后续页不穿入新提现；状态和 `longUnsettled` 等可变筛选按每页当前值重算，客户端需要完整当前队列时刷新首屏。
- 详情分别返回本地 `status`、可空 `channelState`、渠道前暂停/提交后风险、长时间未结算、审核决定、可空安全失败原因和 `nextActions`；API 错误码、微信单据状态和终态失败原因使用不同字段。

### 2. 审核

```http
POST {organizationBase}/withdrawals/{withdrawalNo}/reviews
Idempotency-Key: <UUIDv4>
```

通过：

```json
{
  "expectedVersion": 0,
  "decision": "APPROVED",
  "reason": null
}
```

驳回只把 `decision` 改为 `REJECTED`。请求不允许包含新金额、收款人、渠道状态或机构额度。

- 只允许 `PENDING_REVIEW` 且尚无审核决定。审核员可以审核自己的业务记录。
- 用户钱包因后来资金变动处于负余额暂停时，不允许审核通过；仍可驳回。余额恢复后原单可按当前版本审核。
- 平台出款闸门暂停不阻止审核通过：通过事务只推进 `READY_TO_SUBMIT` 并创建唯一提交任务，任务等待闸门恢复后再从完整锁根复核。
- 驳回事务推进 `REJECTED`，原子释放双方冻结、追加双方最终释放明细并删除活动槽。
- 通过不调用微信、不创建成功资金明细，也不代表转账已经受理。

### 3. 客服处置

```http
POST {organizationBase}/withdrawals/{withdrawalNo}/pre-channel-terminations
POST {organizationBase}/withdrawals/{withdrawalNo}/channel-queries
POST {organizationBase}/withdrawals/{withdrawalNo}/channel-cancellation-requests
Idempotency-Key: <UUIDv4>
```

三类请求都要求 `withdrawal.handle`、`expectedVersion` 和可空原因：

- **渠道前终止**：只允许提现仍为 `READY_TO_SUBMIT` 且数据库不存在微信转账单。成功推进 `LOCAL_ABORTED_BEFORE_CHANNEL`，释放双方冻结并删除活动槽，返回 `200`。只要转账单已经建立，即使网络调用尚无响应，也不得使用本接口。
- **主动查单**：要求已存在固定微信转账单，创建或唤醒使用原 `outBillNo` 的唯一可靠查单任务，返回 `202 + statusUrl`。GET 查询本身不得偷偷触发外部调用。
- **微信撤销请求**：只对微信当前仍允许撤销的非终态原单创建可靠撤销任务，返回 `202`。撤销请求被受理只推进渠道原始状态，不能释放资金；最终 `CANCELLED` 才结算释放，并必须兼容撤销竞争后实际 `SUCCESS`。

进入 `CHANNEL_PROCESSING` 30 分钟仍无微信终态时，只设置一次 `longUnsettledAt`、进入查询/告警筛选并继续接收回调。它不改变业务状态、不释放资金、不生成新单，也不自动发起撤销。

主要错误包括：

```text
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
409 WITHDRAWAL.VERSION_CONFLICT
409 WITHDRAWAL.REVIEW_ALREADY_DECIDED
409 WITHDRAWAL.STATE_CONFLICT
409 WITHDRAWAL.CHANNEL_BOUNDARY_CROSSED
409 WITHDRAWAL.CHANNEL_ORDER_MISSING
422 WITHDRAWAL.NEGATIVE_BALANCE_PAUSED
422 WITHDRAWAL.CHANNEL_ACTION_NOT_ALLOWED
```

## I-035 微信商家转账与公共出款闸门

**已确认：P0 使用普通商户 APIv3“商家转账”的用户确认收款模式；一个提现只使用一个固定外部单号，只有微信三个终态可以驱动双方资金最终结算。**

### 1. 渠道就绪边界

- 系统商户号、获批“二手回收”场景 ID、固定报备内容、APIv3 凭证引用和通知地址属于平台部署/商户配置，不由机构请求体提交。真实私钥和 APIv3 密钥不进入业务库、响应、日志或版本库。
- 每个机构 AppID 必须已在微信侧与系统商户号绑定，并在 EcoBin 记录为 `VERIFIED`。普通资金页只读取该状态；不能用 AppID 字符串存在代替渠道关系已经生效。
- 平台管理员使用下列平台专用接口在完成外部绑定核查后建立或禁用本地就绪事实；它们不替微信完成绑定，也不允许租户自报 `VERIFIED`：

```http
GET  /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/wechat-merchant-binding
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/wechat-merchant-binding/verifications
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/wechat-merchant-binding/disablements
```

验证请求携带当前机构小程序 `expectedMiniappVersion`、可空 `expectedBindingVersion` 和可空核查备注；首次验证时绑定版本为空，更新既有事实时必须精确匹配。禁用请求携带当前 `expectedBindingVersion` 和可空原因。服务端从当前小程序配置读取 AppID，不接受客户端另传 AppID 或商户号。`expectedMiniappVersion` 只用于防止核查期间配置被并发改写；以后仅 AppID 与已验证快照不一致时旧绑定失效，AppSecret 或展示资料变化不要求重新验证商户绑定。

绑定禁用只阻止新的充值创建和新的微信转账提交，不回退已经可靠越过外部调用边界的支付/转账，也不阻止其回调、查单和真实终态结算。对已创建但 Native 下单尚未确定是否外调的充值，只有能证明从未外调时才可本地关闭；否则继续使用原 `outTradeNo` 查单或续办。

### 2. 固定转账请求

审核通过后的可靠执行器在调用微信前，先按“租户/机构/小程序/机构用户身份前缀 → 平台闸门 → AppID/系统商户绑定 → 钱包 → 活动提现 → 提现单 → 机构账户 → 微信转账”锁序复核：

- 提现仍为 `READY_TO_SUBMIT`，没有负余额暂停，活动槽和双方冻结完整；
- 平台闸门开放，机构 AppID/系统商户绑定仍有效；
- 租户、机构、小程序和当前机构用户仍有效，且当前用户仍可由本提现固化的 AppID/OpenID 收款。提现配置变化不改写本单金额范围快照，也不要求提交阶段反向锁当前配置 head。

随后在一个短事务创建唯一微信转账单、全平台唯一且最长 32 位字母数字 `outBillNo`、不可变请求快照，把提现推进 `CHANNEL_PROCESSING` 并建立外部调用边界。提交后才能调用微信商家转账。

P0 请求快照固定包含：

- 机构 AppID及该 AppID 下的用户 OpenID；
- 整数分金额，不传 `user_name`；
- 系统已获批的“二手回收”场景 ID；
- 报备类型“回收商品名称”、内容“混合可回收物”；
- 普通收款确认页；
- 不超过渠道长度的 `EcoBin回收款 + 提现单短标识` 备注；
- 公网 HTTPS、无查询参数的商家转账通知地址。

连接器必须保存微信返回的 `transferBillNo`、原始 `state` 和可空 `packageInfo`。HTTP 非成功、超时、`SYSTEM_ERROR`、限频、未知错误、`ALREADY_EXISTS` 和 `NOT_ENOUGH` 都不能生成新 `outBillNo` 或本地失败终态；只保存技术尝试/渠道观察，并使用原单查单或原参数续办。

### 3. 用户确认收款

```http
GET /api/v1/miniapp/me/withdrawals/{withdrawalNo}/collection-confirmation
Authorization: Bearer <aud=miniapp token>
```

- 只允许提现固化的原机构用户在原 AppID 会话下读取；其他主体、AppID 或机构统一返回 `404 RESOURCE.NOT_FOUND`。
- 微信当前状态必须为 `WAIT_USER_CONFIRM`，本地没有相反终态，且 `packageInfo` 已可靠保存。成功返回 `appId + packageInfo + withdrawalNo + channelState` 并设置 `no-store`；其他状态返回明确不可调起错误。
- 小程序使用该参数调起微信官方确认收款页。前端成功、取消、失败或页面返回都只代表交互结果，不能更新提现或结算资金；最终结果只能来自可信回调、查单或对账。
- 用户超过 24 小时未确认也不能按本地时钟释放。系统继续用原单查微信，直至得到 `FAIL/CANCELLED/SUCCESS`；原单未终结时禁止创建替代提现。

### 4. 渠道状态归并

商家转账通知进入 `/api/v1/wechat-pay/notifications/merchant-transfers`，同样只有在验签/解密完成且唯一 inbox 与 `PROCESS_INBOX` 任务共同提交后才确认收件；领域归并失败由内部任务重试。回调、提交响应、主动查单、撤销响应和对账全部追加不可变 observation，并进入同一状态归并器：

| 微信原始状态 | 渠道终态 | 本地处理 |
|---|---|---|
| `ACCEPTED/PROCESSING/WAIT_USER_CONFIRM/TRANSFERING/CANCELING` | 否 | 提现保持 `CHANNEL_PROCESSING`，双方继续冻结 |
| `SUCCESS` | 是 | 双方冻结各减少提现金额；用户进入 `WITHDRAWAL_SUCCEEDED` 明细，机构额度完成成功消耗，提现置 `SUCCEEDED` |
| `FAIL` | 是 | 双方冻结释放回可用并追加 `WITHDRAWAL_RELEASED`，提现置 `CHANNEL_FAILED`，保存安全失败原因 |
| `CANCELLED` | 是 | 双方冻结释放回可用并追加 `WITHDRAWAL_RELEASED`，提现置 `CHANNEL_CANCELLED` |

- 必须保留微信官方原始拼写 `TRANSFERING`。未知新状态原样保存、停止自动资金归并并建立对账异常，不能当作失败。
- 终态结算事务同时更新用户、机构、提现、活动槽和双方唯一 `FINAL` 明细；重复或乱序来源最多结算一次。相反终态已经存在时不覆盖、不反向调账，只创建对账异常。
- 归并前按来源实际具备字段核对系统商户、双单号、金额、AppID 和 OpenID。缺少某字段不能猜测补齐，字段矛盾不能推进业务状态。
- 微信转账单主动查单窗口为最近 30 天；长期证据必须保存于本地并由资金账单/每日交易级对账补强。30 天后查不到接口单据不能被解释为失败或用新单替代。

### 5. `NOT_ENOUGH` 与平台出款闸门

平台端点：

```http
GET  /api/v1/web/platform/payout-gate
POST /api/v1/web/platform/payout-gate/restorations
```

第一次可信 `NOT_ENOUGH` 观察在完整资金锁序内：

1. 保持当前提现 `CHANNEL_PROCESSING` 和双方冻结，不把错误码伪装成 `FAIL`。
2. 追加唯一 `PAUSED` 闸门事件，把系统商户闸门推进 `PAUSED_NOT_ENOUGH` 并触发同一聚合告警；重复不足只更新告警观察次数。
3. 不修改、冻结、清零或怀疑其他机构未占用额度；已经在微信处理中的其他原单继续回调和查单。
4. 暂停后，新手动提现在创建前直接拒绝且不建单；已创建但尚未提交微信的任务等待恢复。待审核单仍可审核通过或驳回。

公司补足运营账户后，平台管理员提交：

```json
{
  "expectedGateVersion": 7,
  "pausedEventUid": "06e6b087-...",
  "fundsReplenishedConfirmed": true,
  "reason": null
}
```

成功事务只对精确当前暂停事件追加唯一 `RESTORED`、打开闸门并写审计；不增加任何机构额度、不修改提现或伪造渠道状态。提交后唤醒原不足单及等待提交任务，它们仍从完整锁根复核并使用原单号/原参数续办。

主要错误包括：

```text
404 RESOURCE.NOT_FOUND
409 FUNDS.PAYOUT_GATE_VERSION_CONFLICT
409 FUNDS.PAYOUT_GATE_TARGET_CHANGED
409 WITHDRAWAL.CHANNEL_STATE_CONFLICT
422 FUNDS.PAYOUT_GATE_RESTORE_NOT_CONFIRMED
422 WITHDRAWAL.COLLECTION_CONFIRMATION_UNAVAILABLE
```

## 微信官方契约依据

本章的渠道状态和参数边界以普通商户 APIv3 官方文档为准：

- [Native 支付开发指引](https://pay.weixin.qq.com/doc/v3/merchant/4012791891)：Native 下单取得 `code_url`，支付成功通知与主动查单共同确认结果，设置 `time_expire` 后仍需查单/关单收敛。
- [商家转账开发指引](https://pay.weixin.qq.com/doc/v3/merchant/4012715211)：用户确认收款、原单续办、撤销非终态、24 小时确认和 30 天查单窗口。
- [发起转账 API](https://pay.weixin.qq.com/doc/v3/merchant/4012716434)：普通商户请求字段、固定外部单号、`package_info`、原始状态及 `NOT_ENOUGH` 等 API 错误边界。

官方以后增加字段、状态或校验时，连接器必须保留未知原值并停止不安全的自动归并；不得在未更新本章/机器 Schema 和契约测试前，把新增值映射成既有终态。

## 本批落实约束

1. Web、小程序只调用 EcoBin 领域接口，不能持有商户私钥、APIv3 密钥或直接请求微信商户 API。
2. Native 支付和商家转账使用相互独立的渠道单、通知入口、状态机和观察表，只共享签名、验签、HTTP 客户端、系统商户配置和可靠任务基础设施。
3. 两类通知均须保存唯一可信 inbox、原始密文摘要、验签身份和接收时间；业务观察只保存规范化安全字段。敏感原文按保留策略受限存储，不进入普通业务查询。
4. 连接器请求、回调和查询的机器可读 Schema、官方测试向量、签名/验签失败样本、金额/身份矛盾、重复/乱序、原单重试和终态竞争必须进入后续契约测试章节。
5. M0 真机验收至少完成一笔真实 Native 充值到账及净额入账、一笔真实商家转账并由用户确认微信零钱到账；破坏性 `FAIL/CANCELLED/NOT_ENOUGH`、超时和乱序由 Fake/Stub 加真实 MySQL 并发测试覆盖。
