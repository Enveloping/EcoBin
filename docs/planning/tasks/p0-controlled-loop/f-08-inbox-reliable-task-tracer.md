---
task_id: F-08
title: inbox 与可靠任务 tracer
status: in-review
executor: agent
owner: "Codex / reliability-operations-owner"
effort_range: "3-5 person-days"
earliest_start: "F-03、F-06 均 done 后"
blocked_by:
  - F-03
  - F-06
implementation_authorized: true
---

# F-08｜inbox 与可靠任务 tracer

> F-03、F-06 已完成；项目负责人已于 2026-07-26 明确授权 Codex 接取并实施
> F-08。Fake tracer、稳定技术端口和 MySQL 8.4 验收均已完成，任务处于
> `in-review`，等待主审确认。

## 目标

建立中心侧最薄可靠执行链，用一条 Fake 入站消息证明可信规范化、inbox 与任务原子建立、
租约领取、业务事务、attempt 和稳定确认可以在崩溃、重复与并发下收敛。

## 要构建什么

- 实现 operations 的可信收件端口和稳定外部 ID/规范摘要校验。
- 在一个事务中建立或复用 inbox 与 `PROCESS_INBOX` 唯一可靠任务。
- 实现有界批次、数据库时间、租约和过期接管的 task 领取器。
- 将领取短事务、业务领域事务、外部调用观察和最终归并分开。
- 实现稳定 `taskKey`、payload 摘要、attempt、`wakeVersion` 和 blocked reason。
- 建立设备/OneNet、资金/微信和 maintenance 的隔离执行通道骨架及有界配置。
- 使用真实 MySQL 8.4 验证重复、崩溃、迟到、租约和业务回滚。

## 验收条件

- [x] 首次可信收件在同一事务建立 inbox 和唯一处理任务。
- [x] 同稳定 ID、同摘要复用原事实；同 ID、异摘要进入隔离。
- [x] 收件事务提交后才可返回传输 ACK。
- [x] ACK 前后崩溃均可安全重投。
- [x] 过期租约可由另一 worker 接管，不产生第二业务意图。
- [x] 业务回滚不会误标 inbox、attempt 或 task 完成。
- [x] task 行锁不跨领域长事务或外部调用持有。
- [x] 迟到领域事实通过原任务 `wakeVersion` 唤醒。
- [x] 关键测试使用真实 MySQL 8.4，而不是以 H2 代替锁和约束语义。

## 阻塞与最早开始

- [F-03](f-03-business-module-boundary-migration.md) 和
  [F-06](f-06-database-v6-v10-funds-operations.md) 均已完成，operations 模块边界和
  V7 可靠任务表已经具备，任务依赖已解除。
- 项目负责人已于 2026-07-26 明确授权实施，当前不存在领取阻塞。

## 排除范围

- 具体投递、清运、配置、充值或提现领域处理器。
- 真实 OneNet、COS 或微信外部调用。
- 完整 operations 管理页面、告警和对账业务。
- 以 task `DONE` 代替领域或外部终态。

## 权威来源

- [详细设计 03：可靠任务与边缘契约](../../detailed-design/03-reliable-edge-contracts.md)
- [D-026～D-030：operations](../../database-design/06-operations-d026-d030.md)
- [D-036～D-040：事务与并发](../../database-design/08-transactions-concurrency-d036-d040.md)
- [I-036～I-040：运营治理接口](../../interface-design/08-operations-audit-alert-reconciliation-statistics-i036-i040.md)
- [I-041～I-045：OneNet、COS 与边缘确认](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-26：F-03、F-06 均为 `done`，项目负责人明确授权 Codex 接取并实施
  F-08；任务转为 `in-progress`，后续修改在独立 worktree
  `database-refactor-f08-reliable-task` 的 `codex/f08-reliable-task` 分支进行。
- 2026-07-26：完成可信 Fake 收件、规范摘要、inbox/task 原子建立、重复唤醒与冲突
  隔离、租约/attempt/退避/blocked、旧 worker 迟到保护、业务事务共同完成、
  `wakeVersion` 稳定端口和三类有界通道配置。Java 21 全仓回归与制品安装通过；
  MySQL 8.4.10 专项测试全部实际执行且无跳过。验收证据见
  [F-08 实施证据](../../../architecture/f-08-inbox-reliable-task-evidence.md)，任务转为
  `in-review`，等待主审确认。
- 2026-07-26：根据 review 的两个 P1 补强精确数字语义和执行容量边界：JSON 数字改用
  `BigDecimal/BigInteger` 精确解析；runner 改为先获取通道共享在途许可，再逐任务即时
  领取。Java 21 全仓 91 项回归通过，MySQL 8.4.10 专项 5 项全部通过，任务保持
  `in-review`。
