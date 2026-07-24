---
task_id: F-07
title: epoch guard 与空目标库 Fake bootstrap
status: blocked
executor: agent
owner: "TBD / bootstrap-runtime-owner"
effort_range: "2-4 person-days"
earliest_start: "F-03、F-06 均 done 后"
blocked_by:
  - F-03
  - F-06
implementation_authorized: false
---

# F-07｜epoch guard 与空目标库 Fake bootstrap

> `status: blocked` 表示任务依赖尚未完成；`implementation_authorized: false`
> 表示本文件的发布不构成编码授权。

## 目标

让新应用只在正确 V10 目标纪元上启动，并在没有任何业务实例数据的 Fake 环境共同运行；
应用启动不迁移数据库，也不能意外接收真实入口或调用真实设备、COS、微信渠道。

## 要构建什么

- 关闭运行应用的自动 Flyway、baseline 和自动建库。
- 建立只读数据库 epoch/version/description/checksum guard 和 readiness 条件。
- 使用最小运行身份启动正确 V10、无业务数据的目标库。
- 建立 Fake OneNet、COS、微信适配器和配置级真实外联硬阻断。
- 建立空 schema、旧纪元、错误 V1、失败迁移和低版本数据库的拒绝测试。
- 保证基础启动不通过 Mapper 或 seed 偷建后续业务实例。

## 验收条件

- [ ] schema 空库、旧 V1～V14、错误 V1、失败迁移和低于 V10 的数据库均拒绝就绪。
- [ ] 正确 V10 且没有业务实例数据的目标库可以启动。
- [ ] 应用启动不会执行 migrate、baseline、建库、repair 或 DDL。
- [ ] Fake 环境不能接收真实 OneNet/微信入口，也不能调用真实设备、COS 或资金渠道。
- [ ] 启动不会自动创建租户、机构、账号、设备、袋、账户或业务配置。
- [ ] 运行容器不需要 schema owner 或 trigger definer 凭证。
- [ ] guard 与真实外联阻断有自动测试，错误配置严格失败。

## 阻塞与最早开始

- 当前被 [F-03](f-03-business-module-boundary-migration.md) 和
  [F-06](f-06-database-v6-v10-funds-operations.md) 阻塞。
- 必须同时具备最终九模块应用和完整 V10 纪元，才能证明 guard 与 Fake bootstrap 的目标行为。

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
