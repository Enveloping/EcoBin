---
task_id: H-02
title: 目标数据库身份与环境供应
status: ready
executor: human
owner: "unassigned — project owner / database provisioning operator"
effort_range: "1-2 person-days"
earliest_start: "after F-06 is done and explicit operational authorization"
blocked_by:
  - F-06
implementation_authorized: false
---

# H-02｜目标数据库身份与环境供应

## 目标

供应物理隔离的目标 MySQL 8.4 环境和五类最小权限身份，使 V1～V10 可以由一次性迁移作业安装，而运行应用只有获准业务 DML 权限。

## 要执行什么

- 供应物理隔离的 MySQL 8.4 LTS，并固定完整版本和镜像 digest。
- 配置 UTC、严格 SQL mode、目标事务默认值和禁止旧库访问的环境边界。
- 供应实例初始化/恢复管理员、`ecobin_schema_owner`、`ecobin_trigger_definer`、
  `ecobin_app` 和 `ecobin_backup` 五类身份。
- 只在一次性迁移作业中注入 schema owner；运行应用和容器不得获得该凭证。
- 使用 F-06 的完整 V1～V10 执行迁移、运行应用权限正测和 DDL/旧库等权限负测。
- 归档脱敏 grants、数据库版本、迁移结果和权限验证证据。

## 验收与证据

- [ ] MySQL 8.4 实例物理隔离，版本、镜像和 digest 已记录。
- [ ] 时区、SQL mode 和目标连接配置符合冻结设计。
- [ ] 五类身份职责和最小 grants 与设计一致。
- [ ] `ecobin_schema_owner` 和触发器 definer 凭证不进入运行容器。
- [ ] `ecobin_app` 正常业务 DML 和只读 Flyway history 正测通过。
- [ ] `ecobin_app` 执行 DDL、GRANT、触发器管理、受保护删除/更新和旧库访问均失败。
- [ ] 证据全部脱敏，仓库和普通日志中没有数据库秘密。

## 阻塞与最早开始

- 前置 [F-06](f-06-database-v6-v10-funds-operations.md) 已完成，完整 V1～V10、
  权限目录和最小权限矩阵已经可用。
- 当前 `ready` 仅表示任务依赖已解除；项目负责人明确授权目标环境操作后才可开始。
- 完成后解除 H-06 的数据库环境依赖。

## 排除范围

- 使用 root 或 schema owner 运行后端。
- 让应用启动时自动迁移、baseline 或修复目标库。
- 用旧实例中的另一个 schema 代替物理隔离实例。
- 把数据库凭证、完整 grants 中的秘密或真实数据写入仓库。

## 权威来源

- [第 01 章：数据库身份、配置和验证](../../detailed-design/01-foundation-modules-database.md)
- [第 08 章：H-02](../../detailed-design/08-implementation-sequence.md)
- [D-041～D-045：迁移和交付](../../database-design/09-migration-delivery-d041-d045.md)

## 进展记录

- 2026-07-23：正式任务发布；等待 F-06，保持 `blocked`；尚未授权供应或修改数据库环境。
- 2026-07-25：F-06 完成，任务依赖解除并转为 `ready`；真实环境供应仍未授权，
  不创建账号、不执行 GRANT、不接触凭证。
