# EcoBin 微信免确认收款授权数据库设计（D-046）

> 总索引：[database-design-draft.md](../database-design-draft.md)
>
> 状态：**D-046 已确认**
>
> 确认日期：2026-08-06
>
> 2026-08-14 前向修订：明确未被微信受理的创建请求进入
> `CREATE_REJECTED`；旧请求不可重放，用户排除原因后以新操作和新单号重新申请。
>
> 2026-08-25 前向修订：微信普通查单可以不返回 `create_time`。
> `channel_created_at` 只保存微信实际返回的原始渠道事实，禁止用后端时间补值；渠道创建时间
> 缺失时，24 小时确认期限使用已经持久化的后端 `submitted_at`，历史记录或外调后崩溃导致
> 该字段缺失时保守使用不可变的 `created_at`。后续恢复查单或回调的处理时间不得重新起算期限。
>
> 上游决定：所有新提现在创建前必须先具有当前有效的微信免确认收款授权；历史逐笔确认提现继续按原快照收敛。

## D-046 决策

微信商家转账的用户授权不是提现单上的临时按钮状态，而是独立、长期、可关闭的渠道授权聚合。funds 同时保存当前授权投影和每次可信渠道观察；提现单与微信转账单只引用一份已经生效的不可变授权身份快照，不能在审核后或重试时改用另一授权。

该决策仅把“用户授权免确认模式”提前进入当前手动提现范围，不引入投递后自动提现、提现免审、自动恢复出款闸门、机构额度人工调整或微信终态人工修改。

## 1. 表与所有权

| 表 | 写类 | 核心事实 |
|---|---|---|
| `fund_wechat_transfer_authorization` | 受保护当前投影 | 机构用户、商户/小程序绑定、AppID/OpenID/场景和展示名称请求快照，商户授权单号、微信授权单号、微信原始状态、本地状态、授权页面参数、关闭原因、请求摘要和阶段时间。 |
| `fund_wechat_transfer_authorization_observation` | 只追加 | `CREATE_RESPONSE/CALLBACK/QUERY` 的实际返回字段、来源 inbox 或任务尝试、原始状态/错误码/关闭原因、授权双单号、AppID/OpenID/场景、展示名称、用户收款感知、渠道时间和内容摘要。 |

integration 不拥有授权表，只负责 APIv3 请求、签名验签、通知解密和规范化。授权应用用例与提现继续由 funds 拥有。

## 2. 授权状态与当前唯一性

微信原始状态保持可扩展字符串，当前已知值为：

```text
WAIT_USER_CONFIRM
TAKING_EFFECT
CLOSED
```

本地投影固定为：

```text
CREATED ──明确未受理──→ CREATE_REJECTED
   └──────微信受理────→ WAIT_USER_CONFIRM → ACTIVE → CLOSED
                              ├────────────→ EXPIRED
                              └────────────→ UNKNOWN
```

- `CREATED` 只表示本地固定了授权请求和可靠任务，尚无微信受理事实。
- `CREATE_REJECTED` 表示创建接口已经返回可验证的明确拒绝，例如
  `PARAM_ERROR`。它对外展示为 `FAILED`，旧请求快照、原商户授权单号、观察和阻断任务都
  永久保留，但不再占当前授权槽，也不得恢复原创建任务再次外调。用户排除参数或配置原因后，
  使用新的幂等操作号生成新的 `out_authorization_no`。网络中断、响应验签失败或其他无法证明
  “微信未受理”的情况不得进入该状态，而应进入 `UNKNOWN` 并继续占槽、按原单查证。
- `WAIT_USER_CONFIRM` 必须保存仍可展示的 `package_info` 和稳定的 24 小时确认期限；
  `channel_created_at` 仅在微信实际返回 `create_time` 时保存，可以为空。普通查单既没有可用
  展示包也无法从可信创建事实恢复时进入 `UNKNOWN` 并继续查终态，不能拼造展示参数或渠道时间。
  同一商户授权单号一旦保存了渠道创建时间，后续不同的非空值只能进入观察记录并触发证据冲突，
  不得覆盖首次值或改变既有确认期限。后续缺失该可选字段不能解除冲突；只有再次返回首次值才可
  自动解除。若先取得 `CLOSED` 终态，授权业务可以关闭，但时间矛盾仍作为未解决对账问题保留。
- 只有微信 `TAKING_EFFECT` 且返回的授权身份与本地快照完全一致，才能进入 `ACTIVE` 并保存非空 `authorization_id`。
- `CLOSED` 必须保存微信关闭时间和原始关闭原因；关闭后不能再用于创建提现或提交转账。
- `EXPIRED` 只处理服务离线跨过微信保留期的恢复：本地已有可信 `WAIT_USER_CONFIRM` 创建事实、确认期限已超过 30 天、从未取得授权单号或生效证据，且原单查单明确返回 `NOT_FOUND` 时，才以 `USER_OVERDUE_UNCONFIRMED_AFTER_RETENTION` 释放当前槽。普通 404、刚过 24 小时或查询暂时失败不得推断过期。
- 未知状态、身份矛盾、可信来源之间的相反状态进入 `UNKNOWN`，停止自动创建提现和自动渠道归并，并建立对账异常。若相反证据指向已经 `CLOSED/EXPIRED` 的旧授权且同作用域已有较新的当前授权，旧行保留终态并设置 `state_conflict=1`，较新的当前行改为 `UNKNOWN`；这样既不违反当前槽唯一键，也能阻止继续提现。若没有较新当前行，旧行自身进入 `UNKNOWN` 并重新占槽。

授权历史允许多条，但同一 `merchant_profile_id + appid + openid + scene_id` 最多一条 `CREATED/WAIT_USER_CONFIRM/ACTIVE/UNKNOWN`。V35 使用终态返回 `NULL` 的 `current_authorization_slot` 生成列和唯一键实现当前槽；V51 将 `CREATE_REJECTED` 明确为不占槽的本地终态，`CLOSED/EXPIRED` 历史同样不占当前槽。未知状态继续占槽，防止系统在事实不明时创建第二份授权。

## 3. 强关系与不可变快照

授权行必须同时强引用：

- 当前租户和机构；
- 当前机构用户及其 `organization_miniapp_id + openid` 身份；
- 平台普通商户及 `mchid`；
- 已核查的机构小程序/系统商户绑定及 `appid`。

V35 保留原有 `(organization_miniapp_id, openid)` 唯一键，并为该强关系增加复合候选键 `(tenant_id, organization_miniapp_id, openid, organization_id, id)`，再建立授权外键；不能把“用户 ID 存在”和“另一个 AppID/OpenID 存在”拆成两条弱外键后假定它们属于同一用户。

`out_authorization_no` 全平台唯一；非空 `authorization_id` 全平台唯一。授权请求的 AppID、OpenID、场景、用户展示名称、可空用户收款感知、HTTPS 无查询参数通知地址及请求摘要创建后不可修改。

`fund_withdrawal_order` 和 `fund_wechat_transfer` 增加：

```text
collection_mode_snapshot
transfer_authorization_id
out_authorization_no_snapshot
authorization_id_snapshot
```

字段形状固定为：

- `USER_CONFIRM`：三项授权引用全部为空，保留历史逐笔确认流程；
- `AUTHORIZED`：三项授权引用全部非空，并通过复合外键证明授权确属同一机构用户、商户、绑定、AppID 和 OpenID。

一笔提现创建后不能把 `USER_CONFIRM` 改成 `AUTHORIZED`，也不能替换授权记录或授权单号。微信转账单必须复制提现单的模式和授权快照；第二条复合外键保证两者不能串用不同授权。

## 4. 授权后转账请求形状

`AUTHORIZED` 使用微信“用户授权后转账”接口。该请求使用 `authorization_id` 或 `out_authorization_no` 定位收款授权，不发送逐笔确认模式的 `user_recv_style`、转账通知地址或 `package_info`。

因此 `fund_wechat_transfer` 的请求形状按模式互斥：

- `USER_CONFIRM` 必须有普通确认页样式和通知地址摘要，授权字段为空；
- `AUTHORIZED` 必须有授权快照，确认页样式和转账通知地址均为空；
- 两种模式都必须保留固定 `out_bill_no`、金额、场景、报备、备注和完整请求摘要。

微信授权后转账的 HTTP 状态和错误码只说明本次请求结果。任何不明确结果仍使用原 `out_bill_no` 查单，不能换号或更换授权重试；只有可信微信转账终态才能结算双方冻结。

## 5. 事务与锁序

授权创建采用：

```text
tenant → organization → miniapp → organization user
→ merchant binding → current authorization slot → reliable task
```

提现创建在原资金锁序中把授权放在绑定之后、钱包之前：

```text
tenant → organization → miniapp → organization user
→ payout gate → withdraw config head/version → merchant binding
→ active transfer authorization → user wallet → active withdrawal
→ withdrawal order → organization account
```

审核通过后的提交 worker 使用同一前缀重新锁定并核对授权仍为 `ACTIVE`，再进入钱包、活动提现、提现单、机构账户和转账投影。授权已关闭、过期、未知或身份变化且尚无微信转账单时不得调用微信；同一事务推进 `LOCAL_ABORTED_BEFORE_CHANNEL`、追加双方释放明细、释放双方冻结并删除活动槽。用户重新授权后创建新提现，禁止替换原提现的授权快照。若微信转账单已经建立，授权后来关闭不修改该转账或其资金收敛。

授权状态查询是外部调用，不能持有上述数据库锁。短事务只固定任务、原授权单号和请求摘要；网络调用后在新事务追加观察并归并。

## 6. V35 基础模型与 V51 前向修订

V35 新增两张表，目标数据库由 97 张领域表推进为 99 张，epoch 从 V34 推进为 V35。

迁移必须先于新应用激活，并保持数据库扩展兼容：

- 历史提现和转账回填/默认 `USER_CONFIRM`；
- 旧应用在数据库升级窗口创建的提现仍可按旧模式写入；
- 新应用上线后必须显式写 `AUTHORIZED`，并在创建提现前要求 `ACTIVE` 授权；
- 不批量把历史在途提现改成授权模式，不重建 `out_bill_no`，不清理旧 `package_info` 证据。

V35 是“先扩表、后切应用”的单向兼容窗口，不承诺新写入后的旧应用回退：第一条授权记录或 `AUTHORIZED` 提现写入前，可以整对退回旧应用；一旦新模式产生权威数据，旧代码可能无法理解空逐笔确认字段，必须停止资金入口并前向修复，不能直接切回旧应用继续写。

运行账号只可更新授权投影的状态、微信授权单号、页面参数、错误、关闭原因、阶段时间和锁版本；请求身份及摘要不可更新。授权观察只允许 `SELECT/INSERT`，不允许 `UPDATE/DELETE`。

V51 不修改任何请求快照或渠道观察，只扩展本地状态约束，并把已有的明确创建拒绝从
`CREATED + 永久错误码` 前向归并为 `CREATE_REJECTED`。已有响应验签失败等不确定结果前向归并为
`UNKNOWN`。应用纪元因此提升到 V51；迁移前的 V50 应用不能与产生 `CREATE_REJECTED` 的新写入并行。

## 7. 官方规则依据

- [发起免确认收款授权](https://pay.weixin.qq.com/doc/v3/merchant/4015901167)：授权申请 24 小时有效，授权成功后长期有效，必须保存商户授权单号和微信授权单号。
- [商户单号查询授权结果](https://pay.weixin.qq.com/doc/v3/merchant/4014399423)：授权状态为 `WAIT_USER_CONFIRM/TAKING_EFFECT/CLOSED`，关闭原因和授权身份通过查单取得。
- [免确认收款授权结果通知](https://pay.weixin.qq.com/doc/v3/merchant/4014512908)：确认和关闭通知可能重复，必须验签解密、核对身份并以主动查询兜底。
- [用户授权后转账](https://pay.weixin.qq.com/doc/v3/merchant/4014399371)：授权后转账使用授权单号，结果不明确时不得换商户转账单号重试。

## 8. 当前实施状态

V35 数据库形状以及 V51 的创建拒绝终态、强约束、最小权限和 epoch 门禁，连同授权端口、微信/Fake 适配器、可靠任务、通知入口、提现状态机、小程序和 OpenAPI 均已在代码中实现。Fresh MySQL V1→V51 迁移和授权提现主路径由自动化验证覆盖。

该状态不等于真实微信渠道已验收。生产环境必须先执行 V35，再启动新应用；第一条 `AUTHORIZED` 权威数据产生后只能前向修复。真实普通商户仍需完成首次授权、关闭竞态、授权后小额转账、主动查单和回调验收后，才可将自动收款视为可上线能力。
