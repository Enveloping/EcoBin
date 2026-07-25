---
task_id: F-03
title: funds、device、recycling、operations 边界搬迁
status: done
executor: agent
owner: "Codex / backend-architecture-owner"
effort_range: "4-7 person-days"
earliest_start: "F-02 done 后"
blocked_by:
  - F-02
implementation_authorized: true
---

# F-03｜funds、device、recycling、operations 边界搬迁

> `status: done` 表示 F-03 已按授权完成旧行为等价的模块边界搬迁；目标新业务仍由
> 后续纵向任务实施。

## 目标

把旧 business 及相关跨域代码迁移到 funds、device、recycling、operations 的唯一事实
所有模块，删除旧业务大模块，并完成最终九模块 Maven DAG；本任务保持旧行为，不开发目标
新业务。

## 要构建什么

- 把当前钱包、提现及相关测试迁入 funds。
- 把资产、部署、投口、设备作业、投递会话、命令和物理证据归入 device；本地继续轮次不进入云端模块。
- 把投递订单、审核、清运、袋、基准、满溢和 P0 查询归入 recycling。
- 把可靠执行、审计和运营基础归入 operations。
- 以窄公开端口替代跨模块 Mapper、Entity、Repository 和内部 Service 引用。
- 收紧 common/framework，删除旧 business，完成 bootstrap 显式装配。
- 建立 Maven 依赖和包导入检查，证明依赖方向符合冻结 DAG。

## 验收条件

- [x] 根构建最终只声明九个目标模块。
- [x] 旧 system/business 不再作为 Maven 模块存在。
- [x] 跨业务模块只导入目标模块 `.api`。
- [x] `UserMapper`、`DeviceMapper`、跨域 Entity 和内部 Service 的直接引用为零。
- [x] common 为小型纯 Java 共享内核，不依赖 Spring、MyBatis 或外部 SDK。
- [x] framework 只保留稳定基础设施和技术端口。
- [x] bootstrap 只负责组装、配置、数据库 guard、seed runner 和跨模块测试。
- [x] 全量构建及旧行为回归测试通过。
- [x] 没有在搬迁中顺带实现目标新业务。

## 阻塞与最早开始

- [F-02](f-02-identity-boundary-and-trusted-context.md) 已完成，identity 的公开边界和迁移
  已稳定，任务依赖已经解除。
- 项目负责人已于 2026-07-25 明确授权，任务已经完成，不再存在实施阻塞。

## 排除范围

- 目标 V1～V10 和数据库切换。
- 新投递、清运、充值、提现或设备状态机。
- Web、小程序和香橙派目标行为。
- 通过共享数据库 Mapper 或 framework 大杂烩维持长期兼容。

## 权威来源

- [详细设计 01：模块、数据库与切换基础](../../detailed-design/01-foundation-modules-database.md)
- [系统架构基线](../../system-architecture-draft.md)
- [I-051～I-055：模块端口与机器契约](../../interface-design/11-module-ports-machine-contracts-i051-i055.md)
- [详细设计 08：实施任务依赖图](../../detailed-design/08-implementation-sequence.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-24：同步 session 一单设计，删除目标云端投递周期事实；任务依赖和授权状态不变。
- 2026-07-24：F-02 完成后任务由 `blocked` 转为 `ready`；尚未获得 F-03 编码授权。
- 2026-07-25：项目负责人明确授权实施 F-03，任务转为 `in-progress`。
- 2026-07-25：旧 `business` 的钱包/提现、投递/清运和统计行为分别迁入 funds、
  recycling、operations，device 提供窄公开查询端口；OneNet 入站只依赖 recycling
  公开事件端口。common 收紧为纯 Java 共享内核，持久化基类移入 framework，旧
  `ecobin-module-business` 从源码、POM 和 reactor 退出。
- 2026-07-25：使用 JDK 21.0.10 完成最终验证：根项目加 9 个目标模块共 10 个
  reactor project 全部成功；23 份 Surefire 报告、82 项测试
  `failures=0`、`errors=0`、`skipped=0`；`mvn.cmd install -DskipTests` 成功。
  新增边界测试固定九模块清单、Maven DAG、跨业务模块仅 `.api`、common 纯 Java 和
  bootstrap 只组装五项门禁。实施证据见
  [F-03 模块边界搬迁证据](../../../architecture/f-03-business-boundary-evidence.md)。
