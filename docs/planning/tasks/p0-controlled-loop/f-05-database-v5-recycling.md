---
task_id: F-05
title: 目标数据库 V5 recycling
status: done
executor: agent
owner: "Codex / backend-database"
effort_range: "3-5 person-days"
earliest_start: "F-04 done 后"
blocked_by:
  - F-04
implementation_authorized: true
---

# F-05｜目标数据库 V5 recycling

> `status: ready` 表示 F-04 前置依赖已经完成；`implementation_authorized: false`
> 表示依赖解除不构成 F-05 编码或迁移授权。

## 目标

在 V1～V4 的身份、设备和证据骨架上建立 V5 recycling，完整承载投递、认定版本、照片、
清运、袋追溯、重量基准、容量和满溢事实。

## 要构建什么

- 编写 `V5__recycling.sql`，准确建立 24 张 recycling 表。
- 落实投递原始事实、订单、异常标记、照片槽位和只追加认定/纠错版本。
- 落实可恢复清运操作、清运记录、袋预留/当前位置历史和旧袋缺失事实。
- 落实空袋重量基准、容量配置、满溢检测、采样和持续事件。
- 建立 V5 的逐表所有权、复合外键、唯一键、CHECK 与索引验证矩阵。
- 在 V1～V4 的全新安装结果上验证严格升级和跨机构隔离。

## 验收条件

- [x] V5 可在完成的 V1～V4 上严格升级。
- [x] V5 准确建立 24 张 recycling 表。
- [x] 投递原始设备事实不会被最终认定值覆盖。
- [x] 一个投递 session 最多关联一笔订单和一个最终物理结果，中间本地轮次不建表、不入订单。
- [x] 负重量可以保存；订单 `negativeWeightAnomaly` 精确复制最终载荷布尔值，不按整场净重补判；照片使用强类型标准槽位。
- [x] 订单、revision、袋位置和满溢检测代际约束完整。
- [x] 袋没有生命周期状态字段，实体袋可重复使用。
- [x] 旧袋缺失可以被记录，但不会伪造旧袋或阻止普通清运的数据构造。
- [x] 两机构的业务关系不能通过外键、唯一范围或可空列组合串联。
- [x] 迁移失败不会被容错 SQL 掩盖。

## 阻塞与最早开始

- [F-04](f-04-database-v1-v4-iam-device.md) 已完成，可重复安装的 V1～V4 已经具备，
  任务依赖已经解除。
- 本任务仍须由项目负责人明确授权后，才能编写和验证 V5。

## 排除范围

- recycling Java 业务实现、Controller、页面和小程序。
- funds、operations 及其跨模块约束。
- seed、环境业务数据和旧数据迁移。
- 在迁移中实现投递审核、清运或满溢业务状态机。

## 权威来源

- [详细设计 01：模块、数据库与切换基础](../../detailed-design/01-foundation-modules-database.md)
- [D-016～D-020：recycling 数据模型](../../database-design/04-recycling-d016-d020.md)
- [D-031～D-035：约束与索引](../../database-design/07-constraints-d031-d035.md)
- [D-036～D-040：事务与并发](../../database-design/08-transactions-concurrency-d036-d040.md)
- [D-041～D-045：迁移与交付](../../database-design/09-migration-delivery-d041-d045.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-24：同步 session 唯一订单、整场四图和最终负重量布尔标志约束；依赖和授权状态不变。
- 2026-07-24：F-04 完成后任务由 `blocked` 转为 `ready`；尚未获得 F-05 编码或迁移授权。
- 2026-07-25：项目负责人授权继续推进后端并允许自行选取任务；Codex 领取 F-05，
  在独立 `codex/f05-recycling` 分支与 `database-refactor-f05-recycling` worktree
  开始实施。
- 2026-07-25：完成 `V5__recycling.sql`、24 表逐表验证矩阵和
  `verify-f05-migrations.ps1`。官方 MySQL 8.4.10 两套空库严格安装 V1～V5 后结构一致：
  54 tables / 189 FK / 180 UQ / 258 CHECK / 186 non-unique indexes；
  189 个外键全部有显式左前缀索引，24 张 recycling 表全部直接引用机构根，
  16 个数据负例和负重量不补判、四照片槽、旧袋缺失清运、负容量原值及 >100% 满溢度
  正例通过。结构 SHA-256：
  `af3684aca14e082afa624a425f2d7aeca675b39dfd8300e66eb5b3f744c40ca7`。
  使用项目要求的 JDK 21.0.10 执行完整 Maven reactor `.\mvnw.cmd test`，
  退出码为 0。
  任务进入 `in-review`，等待主审合并确认。
- 2026-07-25：项目负责人完成审核并确认合并，任务转为 `done`。
