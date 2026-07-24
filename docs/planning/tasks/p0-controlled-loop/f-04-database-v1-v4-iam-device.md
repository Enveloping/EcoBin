---
task_id: F-04
title: 目标数据库 V1-V4 IAM 与设备骨架
status: done
executor: agent
owner: "Codex / database-migration-owner"
effort_range: "3-5 person-days"
earliest_start: "正式实施获授权后，可与 H-01/F-01 并行"
blocked_by: []
implementation_authorized: true
---

# F-04｜目标数据库 V1-V4 IAM 与设备骨架

> `status: done`：目标 V1～V4、30 张 IAM/设备表及验证矩阵已经实施，并在两个全新
> MySQL 8.4 空库完成严格安装、结构一致性和负例验收。

## 目标

在不依赖应用模块搬迁完成的前提下，独立编写并验证目标迁移 V1～V4，建立 IAM、设备资产
与配置、机构用户与会话、设备作业与证据共 30 张表及当时可建立的全部约束。

## 要构建什么

- 编写 `V1__p0_epoch_and_iam_core.sql`。
- 编写 `V2__device_inventory_and_configuration.sql`。
- 编写 `V3__organization_users_and_sessions.sql`。
- 编写 `V4__device_operations_and_evidence.sql`。
- 从数据库基线形成 V1～V4 的“表→所有者→主键/公开键→复合外键→唯一/CHECK→索引”
  验证矩阵。
- 使用两个全新 MySQL 8.4 空库严格安装并比较结构。
- 保持脚本可独立验证；把迁移目录接入目标新应用及联合验收需等待 F-01。

## 验收条件

- [x] V1～V4 可在全新 MySQL 8.4 空库严格安装。
- [x] 四个版本合计准确建立 30 张表，不建立 `dev_delivery_cycle`。
- [x] 表所有权、主键、公开键、复合作用域外键、唯一键、CHECK 和索引符合冻结基线。
- [x] 两机构的复合关系不能串联。
- [x] 不使用 `IF NOT EXISTS`、baseline 或 `FOREIGN_KEY_CHECKS=0`。
- [x] 迁移不包含租户实例、账号密码、设备实例、袋、余额、配置实例或秘密。
- [x] 两个独立空库安装结果结构一致。
- [x] 失败的半成品库不能被当作可继续使用的目标库。

## 阻塞与最早开始

- 本任务按已批准依赖图没有硬任务前置；项目负责人已授权并完成实施。
- [F-01](f-01-nine-module-skeleton-and-integration.md) 已完成，迁移目录的应用构建前置条件
  已满足；本任务的 `blocked_by: []` 保持不变。

## 排除范围

- V5～V10。
- Java Entity、Mapper、应用用例和 API。
- H-02 的真实数据库账号与环境供应。
- 正式 seed、生产库执行或旧业务数据迁移。
- 修改旧 V1～V14。

## 权威来源

- [详细设计 01：模块、数据库与切换基础](../../detailed-design/01-foundation-modules-database.md)
- [D-006～D-010：Schema 与所有权](../../database-design/02-schema-ownership-d006-d010.md)
- [D-011～D-015：身份与设备](../../database-design/03-identity-device-d011-d015.md)
- [D-031～D-035：约束与索引](../../database-design/07-constraints-d031-d035.md)
- [D-041～D-045：迁移与交付](../../database-design/09-migration-delivery-d041-d045.md)
- [F-04 V1～V4 验证矩阵](../../database-design/f-04-v1-v4-verification-matrix.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；设计依赖已满足，但尚未授权实施。
- 2026-07-24：目标表数随 session 一单设计由 31 调整为 30；任务仍为 `ready` 且未授权实施。
- 2026-07-24：项目负责人授权 F-04。MySQL 8.4.10 上两个独立空库均严格安装 V1～V4，
  得到 `30 tables / 78 FK / 84 UQ / 166 CHECK / 79 non-unique indexes`；补齐 F-10
  u32/u16/i32、价格、展示名和命令错误码边界并收窄无来源命令字段后，跨机构和 26 项
  约束负例全部通过，两库结构 SHA-256 均为
  `cef82b4a9772819a1fb01e37bec3b88cd4cecc7ec9231ec55815a03ee42568f7`，任务完成。
