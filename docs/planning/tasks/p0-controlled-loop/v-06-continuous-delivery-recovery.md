---
task_id: V-06
title: 连续投递、断网与迟到结果恢复
status: blocked
executor: mixed
owner: "待指派 - 投递恢复端到端切片负责人"
effort_range: "6-10 person-days"
earliest_start:
  software: "V-04 与 V-08 的 software 阶段完成，session 完成和最终检测 gate 契约可用"
  integration: "V-04、V-08 integration 完成，可控制断网、重启和迟到事件"
  acceptance: "V-04、V-08 均 done，integration 完成并具备真机故障注入证据"
phase_progress:
  software: not-started
  integration: not-started
  acceptance: not-started
blocked_by:
  - V-04
  - V-08
implementation_authorized: false
---

# V-06｜连续投递、断网与迟到结果恢复

## 目标

让同一用户在一次扫码 session 内无需访问云端即可继续投递多轮，并保证结束、选择超时、
断网、边缘或 MCU 重启、报告超时和迟到结果始终只形成一笔订单，不串用户、不上传中间
过程，也不错误开放原投口。

## 要构建什么

实现默认 30 秒本地“继续/结束”选择、session 内本地再次开门、首末重量和整场四图、
负重量阈值布尔锁存、唯一 `DELIVERY_COMPLETE`、最终满溢 gate、整场结果期限、
`pendingDeliveryResultSessionUid`、迟到结果归并和香橙派/MCU/网络重启恢复。

开始 session 仍采用修订后的 PDD-001，由 recycling 按 D-037 协调 identity、投递配置、
funds 钱包和 device。后续“继续投递”不再调用后端，不建立云端 cycle、继续解析任务或
第二次复合授权。

## 验收标准

- [ ] “继续投递”只在已授权 session 内由设备本地状态机再次打开投递门，不产生 OneNet 命令或事件。
- [ ] 中间轮次重量、照片、按钮、开关门过程和减少重量值不上传后端。
- [ ] 一次 session 本地继续 0、1、N 次都只形成一个最终完成事件、一笔订单和四个整场照片槽。
- [ ] 订单使用首次开门前与最终关门后稳定重量，并沿用 session 开始单价。
- [ ] 普通满溢只在整场结束后判断并影响下一 session；硬件安全故障仍阻止本地再次开门。
- [ ] 用户未点击结束时，30 秒选择窗口超时使用最后一次可靠关门重量正常结束。
- [ ] 最终称重暂时不稳定时沿原 session 恢复；明确终态称重故障可靠保存后仍以 `null + 状态/故障码` 上报唯一完成事件并形成系统异常订单，不用 0 或中间重量兜底。
- [ ] 任一本地轮次减少达到配置阈值时锁存 `negativeWeightAnomaly=true`，只随最终载荷上报；后端原样固化，不按整场首末净重补判。
- [ ] 整场结果期限超时不生成零重量或虚假订单，并在安全边界下结束用户等待、保存原投口待处理 session 指针。
- [ ] 可信迟到结果只归原 session 和原用户并强制人工审核；身份/摘要不足进入技术隔离。
- [ ] 迟到结果不能重启旧 session、抢占当前用户或使用当前袋、价格或容量代际。
- [ ] MQTT 断网时已授权 session 可以本地继续、安全结束并保存唯一 outbox。
- [ ] 香橙派或 MCU 重启先执行 HELLO、完整 QUERY_STATE 和事件补收，不自动重放旧开门命令。
- [ ] 同一稳定完成事件在重复、乱序及进程强杀后不重复创建订单。
- [ ] 真机验收覆盖本地继续多轮、选择超时、点击后断网、关门后重启、结果超时、迟到结果和新用户扫码竞争。

## 阻塞与最早开始

| 阶段 | 最早开始条件 |
|---|---|
| software | V-04 与 V-08 的 software 阶段完成，session 完成和最终检测 gate 契约可用 |
| integration | V-04、V-08 integration 完成，并可控制断网、重启和迟到事件 |
| acceptance | V-04、V-08 均完成，integration 通过并具备真机故障注入证据 |

V-04、V-08 是任务整体完成门。Stub 测试不能替代断网、重启和本地多轮真机证据。
发布本任务不代表已经授权修改代码或驱动设备动作。

## 排除范围

- 为中间轮次建立云端 cycle、订单或可靠事件；
- 上传中间重量、照片、继续次数或具体减少值；
- 用普通满溢判断阻止当前用户继续投递；
- 人工修改或伪造迟到结果；
- 将报告超时自动解释为零投递；
- 其他用户接管当前 session；
- 以缓存或最近用户归属迟到订单；
- 任意远程开门；
- V-08 之外的新容量或安全恢复类型。

## 权威来源

- [投递详细设计第 04 章](../../detailed-design/04-delivery-review-wallet.md)
- [清运与恢复第 05 章](../../detailed-design/05-cleaning-fullness-recovery.md)
- [可靠边缘第 03 章](../../detailed-design/03-reliable-edge-contracts.md)
- [实施依赖第 08 章](../../detailed-design/08-implementation-sequence.md)
- [I-021～I-025](../../interface-design/05-delivery-review-wallet-i021-i025.md)
- [I-026～I-030](../../interface-design/06-cleaning-bags-fullness-recovery-i026-i030.md)
- [I-041～I-045](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)
- [I-046～I-050](../../interface-design/10-uart-protocol-i046-i050.md)
- [I-051～I-055](../../interface-design/11-module-ports-machine-contracts-i051-i055.md)
- [D-036～D-040](../../database-design/08-transactions-concurrency-d036-d040.md)

## 进展记录

- 2026-07-23：发布任务文件；仅完成设计与任务拆分，尚未授权实施。
- 2026-07-24：按一次 session 一单和设备本地继续语义重写；依赖、状态和实施授权不变。
