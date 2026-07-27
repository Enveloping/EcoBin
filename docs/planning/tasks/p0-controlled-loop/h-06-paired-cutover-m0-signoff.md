---
task_id: H-06
title: 成对切换、回退演练与 M0 签署
status: blocked
executor: human
owner: "unassigned — project owner / M0 acceptance signer"
effort_range: "2-4 person-days"
earliest_start: "after every declared blocker is done and activation is explicitly authorized"
blocked_by:
  - H-01
  - H-02
  - H-03
  - H-04
  - H-05
  - F-07
  - F-12
  - V-05
  - V-06
  - V-07
  - V-08
  - V-09
  - V-10
  - V-11
implementation_authorized: false
---

# H-06｜成对切换、回退演练与 M0 签署

> H-06 不降低真实门槛。任何强制真实条件未通过时，只能记录准确的非 M0 结论，本任务不得标记 `done`，也不得使用 `M0_COMPLETE`。

## 目标

证明旧应用/旧数据库和新应用/新数据库能够按完整入口所有权成对切换，在真实入口闩锁前完成整对回退演练，并以包含真实设备和真实微信的 12 场景证据包签署 M0。

## 要执行什么

- 复核所有前置任务状态、版本、制品、数据库纪元、试点 seed、外部资源和验收操作者。
- 在非真实渠道环境完成
  `PREPARED → QUIESCING → QUIESCED` 的完整静默过程。
- 越过真实入口闩锁前，以 H-01 恢复单元完成旧应用+旧数据库整对回退演练。
- 单独执行 `ACTIVATED` 所有权切换，覆盖 Nginx/API、OneNet 消费、任务 worker、设备下行和真实渠道能力。
- 生成不含秘密的 evidence manifest，固定所有软件、数据库、边缘、MCU、配置和测试资源版本。
- 执行并审核 M0 十二场景；设备、OneNet、COS、MCU、真实 Native 充值和真实微信零钱到账必须使用真实证据。
- 记录闩锁后停止入口、保护新库和前向修复方案，并由项目负责人作最终签署。

## 验收与证据

- [ ] H-01、H-02、H-03、H-04、H-05 全部为 `done`。
- [ ] F-07、F-12、V-05、V-06、V-07、V-08、V-09、V-10、V-11 全部为 `done`。
- [ ] `PREPARED → QUIESCING → QUIESCED` 静默过程覆盖所有入口和未完成外部调用。
- [ ] 在真实入口闩锁前完成一次旧应用+旧数据库整对回退，且证据可重复核查。
- [ ] `ACTIVATED` 期间 HTTP、OneNet、worker、设备下行和真实渠道始终只有一套所有者。
- [ ] 证据包固定 app commit/artifact digest、数据库纪元、Web/小程序、边缘、MCU、配置和试点稳定身份。
- [ ] 十二个场景全部通过，且每个场景的步骤、稳定业务引用、自动报告、人工证据和结果齐全。
- [ ] 场景 2 真实 Native 充值和场景 8 真实微信零钱到账均标记为 `REAL`。
- [ ] OneNet、COS、真实设备和 MCU 关键场景均使用真实资源与真机证据。
- [ ] 闩锁后策略明确禁止直接重启旧栈，只允许停止入口、保护新库和前向修复。
- [ ] 项目负责人签署 `M0_COMPLETE`；不存在以 `SIMULATED`、部分场景或文案替代真实门槛。

## 阻塞与最早开始

必须逐项完成以下任务：

- [H-01](h-01-legacy-stack-recovery-baseline.md)
- [H-02](h-02-target-database-identities-environment.md)
- [H-03](h-03-fixed-frame-mcu-hil-acceptance.md)
- [H-04](h-04-real-native-recharge.md)
- [H-05](h-05-real-merchant-transfer.md)
- [F-07](f-07-epoch-guard-and-fake-bootstrap.md)
- [F-12](f-12-pilot-seed-orchestration.md)
- [V-05](v-05-delivery-review-wallet-delta.md)
- [V-06](v-06-continuous-delivery-recovery.md)
- [V-07](v-07-real-cleaning-bag-swap.md)
- [V-08](v-08-fullness-baseline-precise-recovery.md)
- [V-09](v-09-native-recharge-software-loop.md)
- [V-10](v-10-manual-withdrawal-software-loop.md)
- [V-11](v-11-operations-governance-overview.md)

全部依赖为 `done` 且项目负责人明确授权真实切换和验收后才能开始。一次失败演练可以形成 `NON_M0` 证据，但不能关闭本任务。

## 排除范围

- 只切 Nginx/HTTP 而保留旧 OneNet、worker、设备下行或渠道所有者。
- 越过真实入口闩锁后直接重启旧应用或旧数据库。
- 使用 Fake 代替真实充值、真实商家转账或微信零钱到账。
- 跳过场景、把部分完成称为 M0，或迁移无需保留的旧业务数据。

## 权威来源

- [第 01 章：成对切换、恢复单元和闩锁](../../detailed-design/01-foundation-modules-database.md)
- [第 07 章：M0 证据包、十二场景和门槛](../../detailed-design/07-clients-operations-acceptance.md)
- [第 08 章：H-06 和里程碑命名](../../detailed-design/08-implementation-sequence.md)
- [D-041～D-045：迁移与交付](../../database-design/09-migration-delivery-d041-d045.md)

## 进展记录

- 2026-07-23：正式任务发布；全部实施和真实验收依赖尚未完成，保持 `blocked`；尚未授权切换或 M0 验收。
