---
task_id: F-07
title: epoch guard 与空目标库 Fake bootstrap
status: done
executor: agent
owner: "Codex / bootstrap-runtime-owner"
effort_range: "2-4 person-days"
earliest_start: "F-03、F-06 均 done 后"
blocked_by:
  - F-03
  - F-06
implementation_authorized: true
---

# F-07｜epoch guard 与空目标库 Fake bootstrap

> 两项 P1 评审问题已完成红/绿回归、生产 JAR 与 MySQL 8.4.10 复验；
> 项目负责人已确认更新文档、提交并合入 `database-refactor`，任务状态为 `done`。

## 目标

让新应用只在正确 V10 目标纪元上启动，并在没有任何业务实例数据的 Fake 环境共同运行；
应用启动不迁移数据库，也不能意外接收真实入口或调用真实设备、COS、微信渠道。

## 要构建什么

- 从运行制品移除 Flyway 运行库和迁移脚本，关闭 SQL init 和自动建库。
- 建立只读数据库 epoch/version/description/checksum guard 和 readiness 条件。
- 使用最小运行身份启动正确 V10、无业务数据的目标库。
- 建立 Fake OneNet、COS、微信适配器和配置级真实外联硬阻断。
- 建立空 schema、旧纪元、错误 V1、失败迁移和低版本数据库的拒绝测试。
- 保证基础启动不通过 Mapper 或 seed 偷建后续业务实例。

## 验收条件

- [x] schema 空库、旧 V1～V14、错误 V1、失败迁移和低于 V10 的数据库均拒绝就绪。
- [x] 正确 V10 且没有业务实例数据的目标库可以启动。
- [x] 应用启动不会执行 migrate、baseline、建库、repair 或 DDL。
- [x] Fake 环境不能接收真实 OneNet/微信入口，也不能调用真实设备、COS 或资金渠道。
- [x] 启动不会自动创建租户、机构、账号、设备、袋、账户或业务配置。
- [x] 运行容器不需要 schema owner 或 trigger definer 凭证。
- [x] guard 与真实外联阻断有自动测试，错误配置严格失败。

## 阻塞与最早开始

- [F-03](f-03-business-module-boundary-migration.md) 和
  [F-06](f-06-database-v6-v10-funds-operations.md) 均已完成，任务依赖已经解除。
- 项目负责人已于 2026-07-26 明确授权实施，任务不存在未解除的领取阻塞。

## 排除范围

- 完整试点 seed；它只由 F-12 最终编排。
- H-02 的环境账号和真实凭证供应。
- 真实 OneNet、COS、微信或真机联调。
- 投递、清运、充值和提现纵向业务。
- 应用启动时自动迁移目标库。

## 权威来源

- [详细设计 01：模块、数据库与切换基础](../../detailed-design/01-foundation-modules-database.md)
- [详细设计 07：客户端、环境与验收](../../detailed-design/07-clients-operations-acceptance.md)
- [D-041～D-045：迁移与交付](../../database-design/09-migration-delivery-d041-d045.md)
- [详细设计 08：实施任务依赖图](../../detailed-design/08-implementation-sequence.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-26：复核 F-03、F-06 均为 `done`，项目负责人明确授权实施 F-07；
  owner 更新为 `Codex / bootstrap-runtime-owner`，任务由 `blocked` 转为
  `in-progress`，在 `codex/f07-epoch-guard` 独立 worktree 开始执行。
- 2026-07-26：完成固定 V1 description/script/checksum 与完整 V1～V10 的只读
  epoch guard、readiness、运行 Flyway/SQL init/自动建库关闭、旧迁移退出制品，
  以及 Fake OneNet/COS/微信装配和真实入口/凭证硬阻断。
- 2026-07-26：固定镜像 MySQL 8.4.10 验证中，正确 V10、83 张领域表、71 行权限
  参考数据且业务实例为 0 时以 `ecobin_app` 就绪；schema 空库、旧 V1～V14、
  错误 V1、失败 V5、仅到 V9、缺失数据库全部拒绝。迁移 owner 在运行前销毁，
  trigger definer 锁定；运行账号 DDL/事实删除失败，所有启动前后 schema/history
  指纹一致。证据见
  [F-07 验证矩阵](../../database-design/f-07-epoch-guard-fake-bootstrap-verification.md)。
  Java 21 共 102 个测试全部通过，生产 JAR 的 Fake 入站和 epoch 测试旁路均无法通过
  `test` profile 绕过。实现转为 `in-review`，等待项目负责人确认。
- 2026-07-26：评审发现外部配置可重新开启打包在 JAR 内的 Flyway，以及 servlet
  context path 可绕过 Fake 入站前缀匹配。两项 P1 均已先建立失败复现，再修复为：
  生产 JAR 不含 Flyway 运行库及目标/旧迁移脚本；过度授权运行账号传
  `spring.flyway.enabled=true` 仍无法迁移空库；Fake 过滤器按应用内路径匹配，
  `/ctx/api/iot/**` 仍返回 503。完整 Java 21 测试更新为 103 项全通过，真实 MySQL
  验证矩阵保持 schema/history 零变更。任务继续处于 `in-review` 等待复审。
- 2026-07-26：项目负责人确认复审结果，要求更新文档、提交并合入
  `database-refactor`；F-07 转为 `done`。
