---
task_id: V-09
title: Native 充值软件闭环
status: blocked
executor: agent
owner: "unassigned — backend/funds vertical-slice owner"
effort_range: "5-8 person-days"
earliest_start: "after V-02 and F-08 are done, and explicit implementation authorization"
blocked_by:
  - V-02
  - F-08
implementation_authorized: false
---

# V-09｜Native 充值软件闭环

> 本任务被发布或进入 `ready` 只表示设计与依赖允许领取，不代表已经授权修改代码、数据库或外部平台。

## 目标

让机构工作人员在 Web 输入充值金额，通过显式 Fake Native 渠道完成扫码模拟，并看到手续费、净额、支付状态和机构账户一次入账。软件闭环必须保持与真实微信相同的可靠任务和状态收敛边界，但不能冒充真实支付。

## 要构建什么

- 在 funds 中实现充值创建、固定 `out_trade_no`、金额和费率快照，以及
  `PENDING_PAYMENT → PAID_PENDING_POST → POSTED` 状态机。
- 通过 operations inbox 和可靠任务统一接收 Fake 回调、主动查单和迟到观察。
- 把“可信支付成功”与“机构净额入账”拆成两个数据库事务；第二事务失败时沿原充值单和原任务恢复。
- 在机构不可变资金明细中唯一追加 `RECHARGE_POSTED`，并更新机构账户投影。
- 在 OpenAPI 和 Web 中完成金额输入、手续费/净额确认、二维码展示和
  `statusUrl` 轮询。
- `dev/test` 只允许显式 Fake；`acceptance/production` 缺真实渠道配置时必须明确
  `CHANNEL_UNAVAILABLE`，不得自动降级或本地成功。
- 使用真实 MySQL 覆盖边界金额、重复、乱序、崩溃和并发入账。

## 验收条件

- [ ] 毛额只允许 1.00～200000.00 元，中心计算和存储全程使用整数分。
- [ ] 手续费固定为 0.6%，向上取整到分；1.00 元和 200000.00 元等边界有自动测试。
- [ ] 同一充值固定一个 `out_trade_no`，网络重试、回调和查单不会换号。
- [ ] 可信支付成功先提交为 `PAID_PENDING_POST`，只有净额明细和账户投影提交后才进入 `POSTED`。
- [ ] 在两个事务之间强制终止进程后，原任务可以恢复且机构额度只增加一次。
- [ ] 重复、乱序、迟到成功和关单竞争不会覆盖成功事实或重复入账。
- [ ] Web 不把 HTTP `202`、二维码生成、任务领取或 Fake 渠道受理显示为充值成功。
- [ ] Fake 软件证据明确标记为 `SIMULATED`，不被 H-04 或 M0 当作真实微信证据。
- [ ] 日志、任务、示例和测试报告不包含商户密钥、完整回调正文或真实支付凭证。

## 阻塞与最早开始

- 被 [V-02](v-02-organization-user-registration-wallet.md) 阻塞：需要已经成立的机构用户、小程序和零余额钱包边界。
- 被 [F-08](f-08-inbox-reliable-task-tracer.md) 阻塞：充值回调、查单和净额补记必须建立在中心可靠收件与任务执行器上。
- 两项均为 `done` 且项目负责人明确授权实施后，任务才可转为 `ready` 或 `in-progress`。
- 完成后解除 V-10 的软件依赖，并为 H-04 提供真实渠道验收对象。

## 排除范围

- 真实 Native 支付、真实回调和真实查单，由 H-04 验收。
- 充值退款、其他充值方式、微信运营账户余额查询、平台收付通和外部商用。
- production 自动启用 Fake，或在渠道缺失时伪造本地支付成功。

## 权威来源

- [第 08 章：实施任务依赖与 V-09](../../detailed-design/08-implementation-sequence.md)
- [第 06 章：充值状态机、可靠任务和 Fake 边界](../../detailed-design/06-funds-wechat.md)
- [I-031～I-035：充值、提现与微信接口](../../interface-design/07-funds-recharge-withdrawal-wechat-i031-i035.md)
- [D-021～D-025：资金与微信数据模型](../../database-design/05-funds-wechat-d021-d025.md)
- [D-036～D-040：事务、锁序和可靠任务](../../database-design/08-transactions-concurrency-d036-d040.md)

## 进展记录

- 2026-07-23：正式任务发布；前置任务未完成，保持 `blocked`；尚未授权实施。
