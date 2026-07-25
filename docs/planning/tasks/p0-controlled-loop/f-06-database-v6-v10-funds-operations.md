---
task_id: F-06
title: 目标数据库 V6-V10 funds、operations 与约束
status: ready
executor: agent
owner: "TBD / database-funds-operations-owner"
effort_range: "5-8 person-days"
earliest_start: "F-05 done 后"
blocked_by:
  - F-05
implementation_authorized: false
---

# F-06｜目标数据库 V6-V10 funds、operations 与约束

> `status: ready` 表示 F-05 前置依赖已经完成；`implementation_authorized: false`
> 表示依赖解除不构成 F-06 编码或迁移授权。

## 目标

完成 V6～V10，使目标数据库达到 83 张表的完整迁移纪元，并建立资金双账本、可靠执行、
跨模块约束、不可变保护和环境无关权限目录。

## 要构建什么

- 编写 `V6__funds.sql`，建立 funds 20 张表。
- 编写 `V7__operations.sql`，建立 operations 9 张表。
- 编写 `V8__cross_module_constraints.sql`，只补此前无法建立的循环或跨模块外键。
- 编写 `V9__immutability_guards.sql`，只建立已冻结的两类不可变触发器。
- 编写 `V10__permission_reference_data.sql`，只写环境无关权限参考数据。
- 完成 83 张表的逐表矩阵和两个全新 MySQL 8.4 空库的一致安装验证。
- 为 H-02 提供可执行的最小权限矩阵，但不供应真实环境账号或凭证。

## 验收条件

- [ ] V1～V10 可从两个全新 MySQL 8.4 空库严格安装，结构一致。
- [ ] 目标表总数准确为 83。
- [ ] 用户钱包和机构账户双账本、充值、提现、微信转账单和平台出款闸门约束完整。
- [ ] inbox、可靠任务、attempt、审计、隔离、告警和对账表约束完整。
- [ ] V8 只补此前不能创建的循环或跨模块关系。
- [ ] V9 只包含 AppID 激活和机构用户注册归因两类冻结触发器。
- [ ] V10 只包含环境无关权限目录，不含业务实例、凭证或秘密。
- [ ] 所有跨模块关系保持内部 `BIGINT` 复合外键和表所有权，不混用公开 UID 外键方案。
- [ ] 任何失败迁移产生的半库都不能通过结构验收。

## 阻塞与最早开始

- [F-05](f-05-database-v5-recycling.md) 已完成，V5 及其验证证据已经具备，任务依赖
  已经解除。
- 本任务仍须由项目负责人明确授权后，才能实施 V6～V10。

## 排除范围

- H-02 的真实 MySQL 实例、账号、GRANT 执行和凭证保管。
- 业务用例、渠道适配、Web 和小程序。
- 正式试点 seed。
- 修改已经在任何持久环境成功应用的迁移。
- 在 Flyway 中写租户、机构、用户、设备、袋、余额或秘密。

## 权威来源

- [详细设计 01：模块、数据库与切换基础](../../detailed-design/01-foundation-modules-database.md)
- [D-021～D-025：funds 与微信](../../database-design/05-funds-wechat-d021-d025.md)
- [D-026～D-030：operations](../../database-design/06-operations-d026-d030.md)
- [D-031～D-035：约束与索引](../../database-design/07-constraints-d031-d035.md)
- [D-036～D-040：事务与并发](../../database-design/08-transactions-concurrency-d036-d040.md)
- [D-041～D-045：迁移与交付](../../database-design/09-migration-delivery-d041-d045.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-24：目标总表数随删除云端 `dev_delivery_cycle` 调整为 83；依赖和授权状态不变。
- 2026-07-25：F-05 审核完成后任务由 `blocked` 转为 `ready`；尚未获得 F-06 编码或
  迁移授权。
