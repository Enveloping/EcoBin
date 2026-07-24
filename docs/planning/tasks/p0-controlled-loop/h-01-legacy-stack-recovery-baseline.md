---
task_id: H-01
title: 旧栈恢复单元和所有权清单
status: done
executor: human
owner: "project owner / Codex-assisted local operator"
effort_range: "1-2 person-days"
earliest_start: "after explicit operational authorization"
blocked_by: []
implementation_authorized: true
---

# H-01｜旧栈恢复单元和所有权清单

> `status: done`：旧栈制品、数据库备份和恢复证据已经固化，并在不连接真实设备与资金
> 渠道的隔离环境完成整对恢复和健康检查。

## 目标

在新栈施工前，把旧应用和旧数据库固化成可以在隔离环境整对恢复的单元，并建立覆盖所有真实入口和运行资源的所有权清单。

## 要执行什么

- 固化旧 Git commit/tag、可运行制品、制品摘要和旧配置模板。
- 保存旧 V1～V14、逻辑数据库 dump、校验和、Flyway history 和关键表行数。
- 盘点旧应用、数据库实例/容器、账号、卷、Nginx/API、OneNet 消费、任务 worker、设备下行和真实渠道入口。
- 在隔离环境恢复旧应用与旧数据库，并完成不连接真实设备和资金渠道的最小冒烟验证。
- 记录操作者、时间、输入、恢复步骤、验证结果和证据位置；所有输出必须脱密。

## 验收与证据

- [x] 旧 commit/tag、制品和摘要可以相互对应。
- [x] 旧 V1～V14、Flyway history、dump、校验和和行数清单齐全。
- [x] 当前旧栈实际存在的实例、账号、卷、配置位置和真实入口所有权均有明确记录；未供应
  的生产资源显式记为“不存在/不适用”，后续新增资源由 H-06 纳入受控台账。
- [x] 在隔离环境完成一次旧应用+旧数据库整对恢复。
- [x] 冒烟验证证明恢复制品能够读取恢复库，且没有连接真实 OneNet、设备或微信。
- [x] 证据不包含 `.env`、密码、设备 Key、APIv3 key、私钥或真实用户敏感数据。

完整脱敏证据见
[H-01 旧栈恢复单元与所有权证据](../../../operations/h-01-legacy-recovery-evidence.md)。

## 阻塞与最早开始

- 没有任务依赖，设计上已经可以领取。
- 只有项目负责人明确授权备份和隔离恢复操作后才能开始。
- 完成后解除 [F-01](f-01-nine-module-skeleton-and-integration.md) 和 H-06 的相应依赖。

## 排除范围

- 把旧业务数据迁入目标库。
- 删除、覆盖或停止当前旧栈。
- 切换真实流量、设备入口或渠道凭证。
- 把秘密、凭证或完整真实数据提交到仓库。

## 权威来源

- [第 01 章：恢复单元、成对切换和闩锁](../../detailed-design/01-foundation-modules-database.md)
- [第 08 章：H-01 依赖位置](../../detailed-design/08-implementation-sequence.md)
- [D-041～D-045：迁移和交付](../../database-design/09-migration-delivery-d041-d045.md)
- [项目上下文](../../../architecture/project-context.md)

## 进展记录

- 2026-07-23：正式任务发布；没有任务依赖，状态为 `ready`；尚未授权执行任何备份或恢复操作。
- 2026-07-24：项目负责人授权 H-01。已固化旧 commit、可运行 JAR、V1～V14、Flyway
  history、逻辑备份、摘要和关键表行数；在独立 schema 完成整对恢复，全部 14 张表行数
  与源库一致，旧应用在禁用真实 OneNet/微信/COS 后健康检查为 `UP`。恢复包保存在仓库外，
  仓库只记录脱敏证据；任务完成。
