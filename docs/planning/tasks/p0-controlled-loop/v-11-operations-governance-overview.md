---
task_id: V-11
title: 告警、审计、对账与一致概览
status: in_progress
executor: agent
owner: "unassigned — backend/operations vertical-slice owner"
effort_range: "6-10 person-days"
earliest_start: "after F-08, V-04, V-07, V-08 and V-10 are done, and explicit implementation authorization"
blocked_by: []
implementation_authorized: true
---

# V-11｜告警、审计、对账与一致概览

> operations 只能治理可靠执行并提供只读视图，不得成为订单、设备、回收或资金的第二套权威事实。

## 目标

让平台和机构工作人员查看并处置可靠任务、隔离项、审计、聚合告警和内部资金对账问题，同时获得与各权威模块一致的最小经营概览。人工确认和处置不得直接篡改业务终态。

## 要构建什么

- 提供可靠任务/attempt 查询和对 `BLOCKED` 原任务的受控恢复。
- 提供隔离消息查询和只追加的 `ACKNOWLEDGED` 人工确认。
- 实现分型操作者、固化作用域、脱敏且只追加的操作审计。
- 实现持续问题聚合告警，严格区分“已确认”和“来源已解决”。
- 实现每日内部资金对账 run、问题、受控查证/重试动作和系统验证解决。
- 通过各模块公开查询端口，以只读 `REPEATABLE READ` 同快照生成最长 31 日概览。
- 完成平台及租户/机构 Web 工作台，以及工作人员小程序当前机构精简只读入口。
- 使用自动测试证明作用域、脱敏、原任务恢复、告警/对账状态和一致快照。

## 验收条件

- [ ] 恢复保留原 `taskUid/taskKey/payload digest/external business ID`，只递增 `wakeVersion` 并追加审计。
- [ ] 隔离确认只做 `OPEN → ACKNOWLEDGED`，不重放消息、不猜测租户或业务事实。
- [ ] 审计只追加，按固化作用域重新授权，且不返回请求正文、手机号、OpenID、AppSecret、密钥或 SQL。
- [ ] 同一持续来源只有一条活动告警；人工确认不恢复设备、容量、资金闸门或其他来源状态。
- [ ] 每个系统商户和业务日只有一个对账 run，遗漏日期可自动补建但不能人工建立第二轮。
- [ ] 可证明的遗漏只唤醒原领域任务；相反终态、单侧资金或证据不足建立问题和告警。
- [ ] 人工不能直接 `RESOLVE` 对账问题或覆盖余额；只有系统重新验证一致后才解决。
- [ ] 概览最多查询 31 个业务日，在同一只读 RR 快照中完成，不写表、不锁行、不外调。
- [ ] 平台、租户、机构和管理小程序的作用域、脱敏和只读能力测试通过。
- [ ] 投递订单数按已形成订单的 session 计数，不把设备本地继续轮次当作订单或经营流量。
- [ ] 没有累计统计表、余额缓存或 operations 私表形成第二套经营真相。

## 依赖与当前实施边界

- [F-08](f-08-inbox-reliable-task-tracer.md) 提供可靠任务和 attempt 基础。
- [V-04](v-04-real-delivery-pending-review.md)、[V-07](v-07-real-cleaning-bag-swap.md) 和
  [V-08](v-08-fullness-baseline-precise-recovery.md) 提供真实设备与回收异常来源。
- [V-10](v-10-manual-withdrawal-software-loop.md) 提供完整资金状态、闸门和对账对象。
- 2026-08-05 已获得明确实施授权，当前不再因这些前置任务阻塞。
- I-039 每日资金对账由产品决定暂缓；它以及客户端页面仍是 V-11 后续工作，当前后端子集为 H-06 提供治理和概览证据，但不能提供完整对账验收证据。

## 排除范围

- 微信官方账单文件归档、复杂 BI、跨租户排行和可修正累计统计表。
- 工作人员小程序写操作。
- operations 直接修改订单、设备、钱包、机构账户或微信终态。
- 人工直接解决告警/对账问题或重建任务、外部单号。

## 权威来源

- [第 08 章：实施任务依赖与 V-11](../../detailed-design/08-implementation-sequence.md)
- [第 07 章：operations、客户端和 M0 证据](../../detailed-design/07-clients-operations-acceptance.md)
- [第 03 章：可靠收件与任务执行器](../../detailed-design/03-reliable-edge-contracts.md)
- [I-036～I-040：运营、审计、告警与对账接口](../../interface-design/08-operations-audit-alert-reconciliation-statistics-i036-i040.md)
- [D-026～D-030：operations 数据模型](../../database-design/06-operations-d026-d030.md)
- [D-036～D-040：事务和一致性](../../database-design/08-transactions-concurrency-d036-d040.md)

## 进展记录

- 2026-07-23：正式任务发布；权威业务来源尚未完成，保持 `blocked`；尚未授权实施。
- 2026-07-24：同步 session 订单统计口径；依赖及授权状态不变。
- 2026-08-05：获得明确实施授权并完成本轮后端子集：可靠任务/attempt/隔离项查询与精确处置、审计查询、告警查询/确认及来源投影、最长 31 个北京时间业务日的跨模块概览、工作人员小程序只读入口、OpenAPI 与生成类型。I-039 每日资金对账由产品明确暂缓；Web/小程序页面和对账调度/查询/处置不在本轮，因此 V-11 仍保持 `in_progress`，不能标记为整体完成。
