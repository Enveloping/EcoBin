---
task_id: F-12
title: 完整试点 seed 编排
status: blocked
executor: mixed
owner: "TBD / bootstrap-integration-owner"
effort_range: "2-4 person-days (software 1-2; integration 0.5-1; acceptance 0.5-1)"
earliest_start:
  software: "V-01、V-03、V-07、V-10 均 done 后"
  integration: "项目负责人提供试点非秘密输入且 seed runner 可运行后"
  acceptance: "全新 V10 Fake 环境可执行首次、重跑和冲突测试后"
blocked_by:
  - V-01
  - V-03
  - V-07
  - V-10
phase_progress:
  software: not-started
  integration: not-started
  acceptance: not-started
implementation_authorized: false
---

# F-12｜完整试点 seed 编排

> F-12 是最终 seed 编排任务，不能前移替代纵向用例。`implementation_authorized: false`
> 表示本文件的发布不构成编码或环境操作授权。

## 目标

在身份、设备、清运和资金正式用例全部可用后，由 bootstrap 通过这些用例幂等编排一个
完整试点环境，为 H-06 的重复验收提供前置条件；seed 不直接写业务表，也不伪造现场或资金
事实。

## 要构建什么

- 在 bootstrap 提供显式试点 seed runner，只调用已经完成的正式应用用例。
- 以稳定输入建立一个试点租户、两个机构、租户主体和必要工作人员、小程序配置。
- 通过设备用例建立试点资产、部署、全部投口和投递配置。
- 通过清运用例建立初始袋、投口绑定和清运配置。
- 通过资金用例建立机构零余额账户、提现配置和必要计数器。
- 接受项目负责人提供的非秘密试点输入；秘密只使用外部引用或运行环境注入。
- 为首次执行、相同输入重跑、冲突输入和缺少可信现场事实建立验收。

## 验收条件

- [ ] seed 只调用正式应用用例，不使用 Mapper、SQL 或内部 Service 直写业务表。
- [ ] 同一稳定输入首次创建；同键同值重跑为 no-op。
- [ ] 同键异值明确失败，不静默覆盖或生成第二套试点事实。
- [ ] 不重置密码，不把秘密写入数据库脚本、日志或任务证据。
- [ ] 不批量创建机构用户或用户钱包。
- [ ] 不伪造零皮重、设备健康、余额历史、订单、充值或渠道成功。
- [ ] 未取得可信重量基准时，投口保持阻断。
- [ ] 在全新 V10 Fake 环境执行后，H-06 所需的非真实渠道试点前置可重复获得。
- [ ] 项目负责人确认输入作用域和输出清单后，Mixed 任务才可完成 acceptance。

## 阻塞与最早开始

- software 被 [V-01](v-01-tenant-organization-staff-login.md)、
  [V-03](v-03-pilot-device-deployment-configuration.md)、
  [V-07](v-07-real-cleaning-bag-swap.md) 和
  [V-10](v-10-manual-withdrawal-software-loop.md) 阻塞。
- integration 必须等待项目负责人提供试点非秘密输入，并完成 software runner。
- acceptance 必须在全新 V10 Fake 环境验证首次执行、重跑和冲突路径。
- 外部排队等待不计入 `effort_range`。

## 排除范围

- 提前实现或绕开任一被依赖纵向切片。
- 直接写业务表的通用测试数据生成器。
- 创建机构用户、用户钱包或虚构历史业务数据。
- H-02 环境凭证供应。
- 真实微信充值/转账、H-06 切换或 M0 签署。

## 权威来源

- [详细设计 01：模块、数据库与切换基础](../../detailed-design/01-foundation-modules-database.md)
- [详细设计 07：客户端、环境与验收](../../detailed-design/07-clients-operations-acceptance.md)
- [D-041～D-045：迁移与交付](../../database-design/09-migration-delivery-d041-d045.md)
- [详细设计 08：实施任务依赖图](../../detailed-design/08-implementation-sequence.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；保持最终 seed 编排位置，所有阶段均尚未开始。
