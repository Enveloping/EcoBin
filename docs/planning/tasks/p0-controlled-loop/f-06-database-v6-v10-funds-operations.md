---
task_id: F-06
title: 目标数据库 V6-V10 funds、operations 与约束
status: done
executor: agent
owner: "Codex / database-funds-operations-owner"
effort_range: "5-8 person-days"
earliest_start: "F-05 done 后"
blocked_by:
  - F-05
implementation_authorized: true
---

# F-06｜目标数据库 V6-V10 funds、operations 与约束

> F-05 已完成；项目负责人已于 2026-07-25 明确授权实施。V6～V10、验证矩阵和
> MySQL 8.4 双空库验收已经完成。

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

- [x] V1～V10 可从两个全新 MySQL 8.4 空库严格安装，结构一致。
- [x] 目标表总数准确为 83。
- [x] 用户钱包和机构账户双账本、充值、提现、微信转账单和平台出款闸门约束完整。
- [x] inbox、可靠任务、attempt、审计、隔离、告警和对账表约束完整。
- [x] V8 只补此前不能创建的循环或跨模块关系。
- [x] V9 只包含 AppID 激活和机构用户注册归因两类冻结触发器。
- [x] V10 只包含环境无关权限目录，不含业务实例、凭证或秘密。
- [x] 所有跨模块关系保持内部 `BIGINT` 复合外键和表所有权，不混用公开 UID 外键方案。
- [x] 任何失败迁移产生的半库都不能通过结构验收。

## 阻塞与最早开始

- 前置 [F-05](f-05-database-v5-recycling.md) 已完成。
- F-06 完成后解除 H-02 的任务依赖；真实数据库身份与环境供应仍须单独获得操作授权。

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
- 2026-07-25：F-05 已完成；项目负责人明确授权实施 F-06，任务进入 `in-progress`。
- 2026-07-25：完成 V6 的 20 张 funds 表、V7 的 9 张 operations 表、V8 的
  29 个后置复合外键、V9 的两类不可变触发器和 V10 的 71 行权限目录；V1～V10
  在两套 MySQL 8.4.10 空库得到相同的 83 表结构，316 个外键全部有显式索引，
  20 个约束负例和 8 类关键正例通过。F-04/F-05 回归及合并后 Java 21 全量 82 测试通过；
  证据见 [F-06 验证矩阵](../../database-design/f-06-v6-v10-funds-operations-verification-matrix.md)，
  任务完成。
- 2026-07-25：审核回归补强提现长时间未结算的 `NULL/UNKNOWN` 防绕过、暂停事件
  的同商户与 `NOT_ENOUGH` 证据外键，并从 transfer 父候选键移除可变商户配置。
  新增三个负例及“历史 transfer 后配置可更新、原快照不变”的正例，MySQL 8.4.10
  双空库验证再次通过。
- 2026-07-25：项目负责人审核确认后合入 `database-refactor`；同步 `CLAUDE.md`、
  项目上下文、文档中心、架构/数据库/详细设计入口和任务索引，合并后 Java 21
  23 suites、82 tests 全部通过。
