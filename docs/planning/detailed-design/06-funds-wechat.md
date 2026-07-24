# 06｜钱包、机构账户、充值、提现与微信渠道

> 上级索引：[EcoBin P0 详细设计与任务拆分](../detailed-design-draft.md)
>
> 状态：**已批准；真实微信验收当前外部阻塞；尚未授权实施**
>
> 审查日期：2026-07-23
>
> 适用基线：R-101～R-125、D-021～D-025、D-036～D-040、I-031～I-035、I-052～I-055

## 1. 本章裁决

| 编号 | 裁决 |
|---|---|
| DD-011 | 当前“提现审核通过即本地结算成功”整体删除；审核通过只进入 `READY_TO_SUBMIT`。 |
| DD-012 | 用户钱包和机构出款账户使用两套不可变明细与当前投影；提现创建、释放和终结始终双侧原子。 |
| DD-013 | 微信外调统一采用固定商户单号、可靠任务、调用前边界、追加观察和统一归并器；未知结果绝不换单或本地判失败。 |
| DD-014 | `NOT_ENOUGH` 只暂停共用系统商户的平台出款闸门，保留原提现及双侧冻结；恢复由平台管理员人工确认并复用原单。 |
| DD-015 | 微信未就绪期间允许显式 Fake 验证软件状态机，但生产缺少真实能力时必须失败为 `CHANNEL_UNAVAILABLE`，不能回退为本地成功。 |

## 2. 当前实现退出边界

现有资金实现不能增量升级：

- 钱包余额直接保存在旧 `sys_user`；
- 没有不可变钱包明细、钱包版本或机构内提交序号；
- 待审核返现与提现冻结概念混用；
- 提现只冻结用户侧；
- 没有机构出款账户和机构资金明细；
- 没有充值单、微信支付单、观察和净额入账；
- 提现审核通过后直接扣减并标记完成，随后才留微信 TODO；
- 没有固定 `out_bill_no`、微信转账单、查单/撤销/回调归并；
- 没有活动提现槽位、负余额暂停、长时间未结算或平台闸门；
- 当前测试把“审核通过”描述成“已经转账”。

这些表、服务、状态和测试都只能作为旧行为证据，不能进入目标资金主链。

## 3. funds 模块施工结构

建议应用用例按业务能力组织：

```text
org.enveloping.ecobin.funds
├─ api/
│  ├─ port/
│  │  ├─ WalletMutationPort
│  │  ├─ NativePaymentChannelPort
│  │  └─ MerchantTransferChannelPort
│  └─ ...
├─ application/
│  ├─ registration/
│  │  └─ FundsOrganizationUserRegistrationParticipant
│  ├─ wallet/
│  │  ├─ AdjustWalletUseCase
│  │  └─ QueryWalletUseCase
│  ├─ recharge/
│  │  ├─ CreateRechargeUseCase
│  │  ├─ MergePaymentObservationUseCase
│  │  └─ PostRechargeNetAmountUseCase
│  ├─ withdrawal/
│  │  ├─ CreateWithdrawalUseCase
│  │  ├─ ReviewWithdrawalUseCase
│  │  ├─ CancelWithdrawalUseCase
│  │  ├─ AbortBeforeChannelUseCase
│  │  ├─ PrepareTransferSubmissionUseCase
│  │  └─ MergeTransferObservationUseCase
│  └─ gate/
│     └─ RestorePayoutGateUseCase
├─ domain/
├─ infrastructure/persistence/
└─ web/
```

名称可以按项目约定调整，但事实所有权和事务边界不得合并成一个宽泛 `WalletService`。

`FundsOrganizationUserRegistrationParticipant` 实现 identity 声明的单一 `OrganizationUserRegistrationParticipant`。它只能加入 identity 已开启的首次注册事务，使用当次强类型机构用户 FK 构造引用创建唯一零余额钱包；不得查询 identity 私表、另开事务、异步补建或在重复登录时再次执行。该 I-053 例外不改变 `funds → identity` 的 Maven 依赖方向。

外部端口由 funds 定义、integration 实现：

```text
NativePaymentChannelPort
  createNativePayment
  queryPayment
  closePayment

MerchantTransferChannelPort
  submitTransfer
  queryTransfer
  cancelTransfer
```

端口请求只使用固定业务单号、不可变请求快照和安全值对象；微信 SDK 类型、HTTP 状态和原始异常不得进入 funds domain。

## 4. 金额表示和手续费

中心数据库和 Java 领域一律使用整数分：

```text
grossAmountCent
feeAmountCent
netAmountCent
withdrawAmountCent
```

HTTP 返回十进制字符串元，例如 `"200.00"`；TypeScript 不使用 `number` 做资金计算。

充值固定费率：

```text
feeRatePpm = 6000
feeAmountCent = ceil(grossAmountCent × 6000 / 1_000_000)
netAmountCent = grossAmountCent - feeAmountCent
```

中间值使用足够宽的整数或 `BigInteger`，再检查结果范围。例如充值 1.00 元：

```text
gross = 100 分
raw fee = 0.6 分
fee = 1 分
net = 99 分
```

充值毛额范围固定：

```text
1.00 ～ 200000.00 元
```

P0 不提供充值退款。任何未来退款都必须新增业务对象和前向迁移，不能反向修改 `RECHARGE_POSTED`。

## 5. 钱包与机构账户

### 5.1 用户钱包

每个机构用户唯一一个钱包，投影至少包含：

```text
availableAmountCent       # 可为负
frozenAmountCent          # 非负
manualRecoveryRequired
recoveryThresholdSnapshotCent
entrySequenceNo
lockVersion
```

每次资金变化写不可变 `fund_user_wallet_entry`：

```text
entryUid
walletId
entrySequenceNo
organizationVisibilitySequenceNo
entryType
availableDeltaCent
frozenDeltaCent
before/after values
sourceType + sourceUid
committedAt
```

同一来源阶段只能入账一次。余额投影必须能由明细解释；对账发现不一致时只建问题，不直接覆盖余额。

### 5.2 机构出款账户

每机构一个独立本地账户：

```text
availableAmountCent >= 0
frozenAmountCent >= 0
lockVersion
```

机构间不得调拨。所有机构虽然共享公司系统商户号，账本和提现冻结仍按机构隔离。

机构资金明细至少包括：

```text
RECHARGE_POSTED
WITHDRAWAL_FREEZE
WITHDRAWAL_SUCCEEDED
WITHDRAWAL_RELEASED
```

“机构出款账户余额”是 EcoBin 本地出款额度，不宣称等于微信运营账户实时余额。

### 5.3 钱包三项视图

小程序展示：

| 项目 | 权威来源 |
|---|---|
| 待审核返现 | recycling 中有归属、待审核、原始金额可靠且大于 0 的投递订单汇总 |
| 可提现余额 | funds 钱包 `availableAmountCent`，允许显示负数 |
| 提现处理中 | 当前进行中提现的用户侧冻结金额 |

三项在一个数据库一致性快照中查询，但不把待审核返现复制为钱包余额。

## 6. 投递审核与钱包参与端口

投递审核/纠错事务由 recycling 发起，funds 只提供受约束参与端口：

```text
applyDeliveryRevisionDelta(
  organizationUserUid,
  revisionUid,
  deltaCent,
  currentStopThresholdCent,
  trustedOccurredAt
)
```

该端口的业务与幂等身份始终是公开 `organizationUserUid` 和 `revisionUid`。DD-004 允许同一次同步调用另行携带按关系强类型、仅限当前线程/事务的 FK 构造引用，用于把已经冻结的内部 `BIGINT` 复合外键写入 funds 自有表；该引用不能替代公开身份，也不得进入日志、任务、缓存、审计或跨事务状态。funds 不能通过它查询跨模块私表，更不能改收裸主键。首次注册参与扩展已经确认由 `identity.api.port` 声明，`FundsOrganizationUserRegistrationParticipant` 只作为 funds 内的单一实现 Bean。

规则：

- `revisionUid` 是唯一幂等来源；
- 差额为 0 时不创建零金额钱包明细；
- 差额非 0 时严格按 `wallet → organization wallet-entry counter → active withdrawal → withdrawal order` 取得涉及的锁，再追加明细并更新投影；
- 新余额达到或低于当前停投阈值时锁存人工恢复闸和阈值快照；
- 新余额小于 0 时同步处理进行中提现的暂停/风险标记；
- 正向资金变化恢复到 `>=0` 时可解除提现负余额暂停，但不能自动清除投递人工恢复闸；
- 事务任何一步失败时，订单修订和钱包变化全部回滚。

人工调账由 recycling 外层协调：

```text
锁 current delivery config head
  → 取得当前阈值
  → 锁 wallet
  → 锁 organization wallet-entry counter
  → 锁 active withdrawal
  → 锁 withdrawal order
  → 校验 expectedWalletVersion 和非零 delta
  → funds 写 adjustment + wallet entry + 投影
  → 按冻结规则处理停投闸和提现联动
  → 同一事务提交
```

funds 不反向读取 recycling。

## 7. 充值状态机

### 7.1 业务状态

```text
PENDING_PAYMENT
  ├─ 可信支付成功 → PAID_PENDING_POST
  │                  └─ 唯一净额入账 → POSTED
  └─ 查单确认未支付且关单成功 → CLOSED/EXPIRED
```

本地 30 分钟到期只触发查单/关单任务，不能单独证明未支付。成功事实一旦成立，迟到创建响应、错误或关单结果不能回退状态。

### 7.2 创建充值

Web 工作人员在当前机构发起，事务：

1. 校验 `recharge.create` 和机构作用域；
2. 校验 AppID 与系统商户绑定为当前 `VERIFIED`；
3. 校验毛额 1～200000 元；
4. 计算并冻结费率/手续费/净额快照；
5. 创建唯一充值单号和 `out_trade_no`；
6. 建立 `CREATE_NATIVE_PAYMENT:<rechargeNo>` 任务；
7. 提交后返回 `202 + statusUrl`。

任务事务外调用微信 Native 下单。`code_url` 只有在原单仍待支付、未到期且无成功事实时才返回 Web；前端只负责生成二维码。

### 7.3 支付成功与入账

回调、主动查单和对账共用同一支付观察归并器。

第一事务：

```text
校验 mchid/appid/out_trade_no/amount/currency
  → 追加 payment observation
  → recharge: PENDING_PAYMENT → PAID_PENDING_POST
  → 创建 POST_RECHARGE_NET_AMOUNT:<rechargeNo>
```

第二事务：

```text
锁 recharge + payment
  → 复核可信成功
  → 锁 organization account
  → 唯一追加 RECHARGE_POSTED
  → 增加 netAmountCent
  → recharge → POSTED
```

第一事务成功、第二事务失败时系统明确停在 `PAID_PENDING_POST` 并由原任务补齐；重复通知最多入账一次。

## 8. 提现状态机

```text
PENDING_REVIEW
  ├─ 审核驳回 → REJECTED
  ├─ 用户合法取消 → LOCAL_CANCELLED
  └─ 审核通过 → READY_TO_SUBMIT
                    ├─ 渠道前受控终止 → LOCAL_ABORTED_BEFORE_CHANNEL
                    └─ 创建微信转账单 → CHANNEL_PROCESSING
                         ├─ SUCCESS → SUCCEEDED
                         ├─ FAIL → CHANNEL_FAILED
                         └─ CANCELLED → CHANNEL_CANCELLED
```

附加标记不是业务状态：

```text
negativeBalancePaused
postSubmissionRisk
longUnsettledAt
```

P0 全部新提现固定进入 `PENDING_REVIEW`。免审阈值固定 0；自动提现、自动恢复和免确认收款授权不进入 DTO 或数据库字段。

## 9. 提现事务

### 9.1 创建并双侧冻结

锁序固定：

```text
tenant
→ organization
→ current miniapp
→ organization user
→ payout gate
→ withdraw config head/version
→ miniapp-merchant binding
→ user wallet
→ active withdrawal slot
→ withdrawal order
→ organization account
```

前置条件：

- 用户已绑定手机号且未冻结；
- 钱包不为负，没有其他活动提现；
- 金额在当前机构配置的手动最低/最高和 200 元硬上限内；
- 用户可用余额和机构可用额度均足够；
- 平台出款闸门开放；
- 当前 AppID/OpenID/商户绑定有效。

同一事务：

- 创建提现单并冻结配置、AppID、OpenID、商户身份快照；
- 创建钱包唯一活动槽位；
- 用户可用转用户冻结；
- 机构可用转机构冻结；
- 双侧各追加 `FREEZE` 明细。

任一条件失败时不建提现单、不写失败记录、不改变任一余额。

上述是读取和加锁顺序；首次创建时活动槽和提现单都尚不存在，钱包永久根先保护“无活动槽”事实。若外键要求先插入提现单再插入槽位，可以在同一钱包锁内按构造需要插入，但其他路径仍不得从提现单反向取得活动槽或钱包。产生用户钱包 `FREEZE` 明细时，还必须在钱包之后、活动槽之前锁 `fund_organization_wallet_entry_counter`；该节点不能因上图简写而省略。

### 9.2 审核

审核员可以审核自己的业务记录。

- 通过：只写审核决定，推进 `READY_TO_SUBMIT`，建立唯一提交任务；
- 驳回：推进 `REJECTED`，双侧原子释放并删除活动槽位；
- 不允许修改提现金额。

平台闸门暂停不阻止把待审核单审核通过，但提交任务保持等待；负余额暂停期间不能审核通过。

审核、驳回、取消和渠道前终止先普通读取不可变关联，再按 D-038 的
`wallet → organization wallet-entry counter（仅产生释放明细时） → active slot → withdrawal order → organization account`
重新进入；operations 审计/任务始终最后写入，不能先锁任务再申请资金锁。

### 9.3 用户取消

只允许本人对以下单据取消：

```text
status = PENDING_REVIEW
and no wechat transfer row
```

推进 `LOCAL_CANCELLED`，双侧释放并删除活动槽。自动提现不属于 P0。

### 9.4 客服渠道前终止

仅允许有权限人员在：

```text
status = READY_TO_SUBMIT
and no wechat transfer row
```

推进 `LOCAL_ABORTED_BEFORE_CHANNEL` 并双侧释放。只要转账行已经创建，即使尚无网络响应，也视为可能调用，禁止本地终止。

## 10. 微信商家转账

### 10.1 建立不可取消边界

提交 worker 从完整锁根复核闸门、绑定、钱包、活动槽位、提现和机构账户，随后在短事务内：

- 创建唯一 `fund_wechat_transfer`；
- 生成全平台唯一固定 `outBillNo`；
- 保存不可变请求摘要；
- 提现推进 `CHANNEL_PROCESSING`；
- 建立不可本地取消的渠道边界。

提交后，执行器在独立 attempt 事务写 `externalCallMayHaveStartedAt`，再调用微信。数据库事务不能跨网络调用。

### 10.2 状态归并

所有提交响应、回调、主动查单、撤销响应和对账观察都追加保存，并进入同一归并器：

| 微信原始状态 | 是否终态 | 本地处理 |
|---|---|---|
| `ACCEPTED/PROCESSING/WAIT_USER_CONFIRM/TRANSFERING/CANCELING` | 否 | 保持 `CHANNEL_PROCESSING` 和双侧冻结 |
| `SUCCESS` | 是 | 双侧冻结结算，提现 `SUCCEEDED`，删除活动槽 |
| `FAIL` | 是 | 双侧释放，提现 `CHANNEL_FAILED`，删除活动槽 |
| `CANCELLED` | 是 | 双侧释放，提现 `CHANNEL_CANCELLED`，删除活动槽 |
| 未知新状态 | 未知 | 原样保存，停止自动资金归并并建对账异常 |

必须保留微信官方拼写 `TRANSFERING`。

HTTP 错误、超时、`SYSTEM_ERROR`、限频、`ALREADY_EXISTS` 和未知错误都不是渠道终态。使用原 `outBillNo` 查单或原参数幂等续办；禁止换号。

### 10.3 用户确认收款

只有微信原始状态为 `WAIT_USER_CONFIRM` 且保存了 `packageInfo` 时，小程序才可获取：

```text
appId
mchId
packageInfo
withdrawalNo
channelState
```

小程序校验 `appId` 等于当前机构小程序，再调用 `wx.requestMerchantTransfer`。调用成功只代表确认页被调起；返回页面后必须查询后端，不能本地标记到账。

### 10.4 长时间未结算

进入 `CHANNEL_PROCESSING` 超过 30 分钟：

- 只设置一次 `longUnsettledAt`；
- 进入查询和告警筛选；
- 不改变业务状态；
- 不释放冻结；
- 不自动发起撤销。

## 11. NOT_ENOUGH 与全平台闸门

所有机构共享公司系统商户，因此一个 `fund_payout_gate` 以系统商户为根：

```text
OPEN
PAUSED_NOT_ENOUGH
```

第一次可信 `NOT_ENOUGH`：

1. 当前提现保持 `CHANNEL_PROCESSING` 和双侧冻结；
2. 追加转账观察；
3. 插入唯一 `PAUSED` 闸门事件；
4. 闸门推进 `PAUSED_NOT_ENOUGH`；
5. 建立/唤醒一条聚合资金不足告警。

暂停期间：

- 拒绝创建新的手动提现且不建失败单；
- 已经 `READY_TO_SUBMIT` 的任务不调用微信；
- 已在微信处理的原单继续接收回调和查单；
- 不冻结、清零或怀疑其他机构的未使用本地额度。

公司补资后由平台管理员在 Web 人工确认恢复：

```text
锁 gate
→ expectedVersion/currentPauseEvent 精确匹配
→ 追加唯一 RESTORED 事件和审计
→ gate → OPEN
→ 唤醒等待任务
```

恢复后任务仍从完整锁根重新复核，并复用原 `outBillNo`。P0 不自动查询运营账户余额，也不自动恢复。

## 12. 可靠任务

资金通道至少包含：

```text
CREATE_NATIVE_PAYMENT
QUERY_NATIVE_PAYMENT
CLOSE_NATIVE_PAYMENT
POST_RECHARGE_NET_AMOUNT
SUBMIT_MERCHANT_TRANSFER
QUERY_MERCHANT_TRANSFER
CANCEL_MERCHANT_TRANSFER
MARK_WITHDRAWAL_LONG_UNSETTLED
PROCESS_WECHAT_INBOX
DAILY_FUNDS_RECONCILIATION
```

任务 payload 只保存稳定业务身份和不可变请求摘要；商户私钥、APIv3 key、证书私钥、OpenID 明文日志和完整回调正文不得进入任务或日志。

回调验签解密成功后先进入 operations inbox；微信通知 ACK 只在 inbox/处理任务事务提交后返回。

## 13. Web 与小程序

### 13.1 Web

M0 资金工作台至少包括：

- 用户详情、钱包三项、明细和人工调账；
- 提现审核队列、详情、查单、合法取消/渠道前终止；
- 机构出款账户和流水；
- 充值金额确认、手续费/净额、Native 二维码和状态轮询；
- AppID 与系统商户绑定核查；
- 平台出款闸门、资金不足告警和人工恢复；
- 最小对账差异列表。

规则：

- 金额用十进制字符串；
- 写操作复用稳定 `Idempotency-Key`；
- 并发命令携 `expectedVersion`；
- `202` 只表示受理，按 `statusUrl` 轮询；
- 不乐观更新余额；
- 不把“审核通过”或“用户打开确认页”显示为成功到账。

### 13.2 小程序

- 钱包展示待审核、可提现、处理中三项；
- 手动提现读取当前机构配置；
- 提现申请使用幂等键；
- 用户只可取消合法的待审核手动提现；
- 提现详情显示 EcoBin 业务状态与微信处理状态，不能合成一个“成功/失败”布尔值；
- `WAIT_USER_CONFIRM` 才显示确认收款入口；
- 确认页返回后轮询后端终态。

工作人员小程序不扩展完整资金管理；当前机构精简统计/告警仍为只读。

## 14. Fake 与真实微信验收

### 14.1 当前可完成

- V6 资金表、状态机、双账本、锁序和并发约束；
- 微信端口、可靠任务、inbox 和统一状态归并器；
- 显式 `fake-wechat` profile；
- Web 充值/提现/闸门页面；
- 小程序确认收款代码分支；
- 回调重复、乱序、超时、`FAIL/CANCELLED/NOT_ENOUGH` 模拟；
- MySQL 并发、进程崩溃、幂等和对账；
- 软件模拟闭环。

### 14.2 当前不能宣称

- 真实 Native 扫码支付和真实回调/查单；
- 真实充值净额入账证据；
- 商家转账被微信受理；
- 用户确认页真实调起；
- 零钱真实到账；
- 真实 AppID/商户绑定核查；
- 真实渠道查单、撤销及错误码行为；
- M0 真实资金闭环。

环境要求：

| profile | 行为 |
|---|---|
| `dev/test` | 只允许 Fake/Stub，网络层硬阻断真实微信域名 |
| `acceptance` | 显式 opt-in、白名单用户/机构、小额上限、外部注入凭证 |
| `production` | 必须真实适配器和完整配置；缺失即启动/就绪失败 |

任何环境都不得在真实能力缺失时自动退回 Fake 或“本地成功”。

## 15. 资金测试矩阵

至少使用真实 MySQL 8.4 覆盖：

1. 同一审核/纠错重试只产生一个 revision 来源钱包明细；
2. 两笔提现并发创建，同钱包最多一个活动槽；
3. 多钱包竞争机构额度，机构冻结不超过可用；
4. 用户/机构双侧冻结、释放或终结任一步失败整体回滚；
5. 审核通过、驳回、用户取消、客服终止和提交并发只一支生效；
6. 负余额纠错与微信提交边界并发；
7. 支付回调和查单同时成功，充值只入账一次；
8. 支付成功后入账任务崩溃，能从 `PAID_PENDING_POST` 恢复；
9. 提交响应、回调、查单、撤销和对账乱序，只有首个可信终态结算一次；
10. 超时/未知错误后始终复用原 `outBillNo`；
11. 多笔 `NOT_ENOUGH` 只产生一个暂停期和聚合告警；
12. 闸门恢复与新提现/旧提交并发按版本线性化；
13. 手续费在 1 元、分界值、20 万元和溢出边界计算正确；
14. 对账发现投影/明细差异只建 issue，不自动覆盖资金。

H2 和单线程测试不能证明这些性质。

## 16. 设计追踪项（已映射到正式任务）

以下本章编号只用于覆盖追踪；正式任务、依赖和状态见
[`p0-controlled-loop`](../tasks/p0-controlled-loop/00-index.md)。

| 草案 ID | 标题 | 类型 | 依赖 | 验收结果 |
|---|---|---|---|---|
| FND-F01 | 钱包、机构账户与不可变双账本 tracer | AFK | 9 模块、V6/V7 | 空钱包初始化、双投影/明细和真实 MySQL 回滚成立。 |
| FND-F02 | 投递审核/纠错到钱包 | AFK | recycling 订单、FND-F01 | 正/零/负认定只按 revision 差额入账一次。 |
| FND-F03 | 人工调账与负余额联动 | AFK | FND-F02、投递配置 head | 调整可审计、停投闸/提现暂停按锁序一致。 |
| FND-F04 | 提现创建、审核、取消和渠道前终止 | AFK | FND-F01、身份 | 双侧冻结、单活动槽、审核不等于到账。 |
| FND-F05 | Native 充值与净额入账 | AFK + 真实支付 HITL | reliable task、微信端口 | 1～20 万、0.6% 向上取整、两事务幂等。 |
| FND-F06 | 商家转账与统一渠道归并 | AFK + HITL | FND-F04、微信端口 | 固定原单、三终态双侧结算、未知态不释放。 |
| FND-F07 | NOT_ENOUGH 闸门和人工恢复 | AFK + HITL | FND-F06 | 全平台暂停、原单冻结、单一告警和版本化恢复。 |
| FND-F08 | Web 资金工作台 | AFK | FND-F03～07 接口 | 充值、审核、账本、闸门只显示真实状态。 |
| FND-F09 | 小程序钱包与确认收款 | AFK + 真机 HITL | FND-F04、06 | 三项钱包、手动提现、确认页后仍查终态。 |
| FND-F10 | 资金并发、故障恢复与每日对账 | AFK + 演练 HITL | FND-F01～09 | 重复、乱序和崩溃不多扣、不多退、不换单。 |
| FND-F11 | 小额真实微信验收 | HITL | 微信条件、FND-F05～10 | 一笔真实充值和一笔真实转账到账，证据与账本一致。 |

## 17. 主审否决项

- 保留或复用旧“审核通过即完成提现”；
- 只冻结用户或机构一侧；
- 用当前余额覆盖代替不可变明细；
- 把待审核返现写入钱包；
- 使用浮点数计算金额/手续费；
- 资金不足时创建大量失败提现单；
- `NOT_ENOUGH`、超时、HTTP 错误或未知状态释放冻结；
- 换 `outBillNo` 重试；
- 前端、确认页返回或微信“受理”直接驱动本地成功；
- 渠道网络调用持有资金数据库事务；
- 回调绕过 inbox 直接结算；
- 充值成功和机构净额入账压成一个不可恢复事务；
- 自动提现、免审阈值或自动闸门恢复重新进入 P0；
- Fake 适配器在 acceptance/production 缺配置时自动生效；
- 只以 H2、Mock 或单线程测试证明资金正确。
