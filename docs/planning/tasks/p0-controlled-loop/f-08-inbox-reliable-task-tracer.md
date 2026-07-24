---
task_id: F-08
title: inbox 与可靠任务 tracer
status: blocked
executor: agent
owner: "TBD / reliability-operations-owner"
effort_range: "3-5 person-days"
earliest_start: "F-03、F-06 均 done 后"
blocked_by:
  - F-03
  - F-06
implementation_authorized: false
---

# F-08｜inbox 与可靠任务 tracer

> `status: blocked` 表示任务依赖尚未完成；`implementation_authorized: false`
> 表示本文件的发布不构成编码授权。

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

- [ ] 首次可信收件在同一事务建立 inbox 和唯一处理任务。
- [ ] 同稳定 ID、同摘要复用原事实；同 ID、异摘要进入隔离。
- [ ] 收件事务提交后才可返回传输 ACK。
- [ ] ACK 前后崩溃均可安全重投。
- [ ] 过期租约可由另一 worker 接管，不产生第二业务意图。
- [ ] 业务回滚不会误标 inbox、attempt 或 task 完成。
- [ ] task 行锁不跨领域长事务或外部调用持有。
- [ ] 迟到领域事实通过原任务 `wakeVersion` 唤醒。
- [ ] 关键测试使用真实 MySQL 8.4，而不是以 H2 代替锁和约束语义。

## 阻塞与最早开始

- 当前被 [F-03](f-03-business-module-boundary-migration.md) 和
  [F-06](f-06-database-v6-v10-funds-operations.md) 阻塞。
- operations 模块边界和 V7 可靠任务表必须同时存在，才能完成 tracer。

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
