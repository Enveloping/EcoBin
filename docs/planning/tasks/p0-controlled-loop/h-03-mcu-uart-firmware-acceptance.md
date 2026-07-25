---
task_id: H-03
title: MCU UART 1.0 固件与真机基础验收
status: blocked
executor: human
owner: "unassigned — MCU firmware owner"
effort_range: "5-10 person-days"
earliest_start: "after F-10 is done, the Registry is handed off, and implementation is explicitly authorized"
blocked_by:
  - F-10
implementation_authorized: false
---

# H-03｜MCU UART 1.0 固件与真机基础验收

> `status: blocked`：MCU 负责人已经接受 Registry 的消息数值、状态机边界、能力位和
> 非易失能力，但 F-10 的三语言黄金样本一致性证据尚未收口，且 H-03 固件实施尚未获得
> 项目负责人明确授权。

## 目标

由 MCU 固件负责人按唯一 UART 1.0 Registry 实现实时控制、可靠事件和安全恢复，并通过真实硬件故障注入证明投递、清运、检测和门安全基础。

## 要执行什么

- 确认并实现 Registry 消息号、字段偏移、能力位、错误码和黄金字节样本。
- 实现严格分帧、CRC-16/CCITT-FALSE、ACK/NACK、停等重试、事件序号和幂等危险命令。
- 持久保存 `mcuBootId`、关键事件队列、当前作业/门阶段、配置摘要和危险命令去重信息。
- 实现配置 staging/COMMIT、投递 session/本地继续、清运电磁阀解锁/再次解锁/
  人工关门确认、满溢、基准测量、投递门 `SAFE_CLOSE` 和状态查询状态机。
- 实现投递门门磁/执行器、清运电磁阀、称重、红外、烟雾的状态、健康和故障映射；
  清运门没有门磁，通断只能推定门状态。
- 实现 `STATE_SNAPSHOT_BEGIN/PORT/END` 冻结快照、分段、摘要和同查询重放。
- 在真实 MCU 与设备上执行重复命令、ACK 丢失、掉电、本地连续投递、清运再次解锁、
  人工关门确认、负重量阈值标志和安全故障验收。

## 验收与证据

- [ ] 固件版本、协议版本、Registry 摘要和 capability bitmap 可相互对应。
- [ ] CRC、长度、帧期限、ACK/NACK、原帧重试和重复危险命令均按黄金向量通过。
- [ ] 看门狗和断电后，关键事件与去重事实仍可恢复；不会自动重放旧开门。
- [ ] 投递 session/本地继续、清运解锁/人工确认、检测、基准和投递门 `SAFE_CLOSE` 状态机通过真机验证。
- [ ] 稳定重量、负重量、过载、不稳定和传感器故障使用明确状态，不以 0 兜底。
- [ ] 快照缺段、同索引异内容或 boot 改变时整份作废，不能用部分状态解除安全锁。
- [ ] MCU 独立超时关门和香橙派失联行为通过。
- [ ] ACK 丢失、重复命令、掉电、清运解锁结果未知和人工确认恢复保存了真机证据。
- [ ] 投递中间轮次不上云；清运电磁阀断电不被固件宣称为真实门磁关门。
- [ ] 非易失介质、容量、擦写寿命和关键事件保留能力有书面说明。

## 阻塞与最早开始

- 被 [F-10](f-10-onenet-schema-uart-registry.md) 阻塞：固件必须使用已冻结的唯一 Registry，而不是两端分别手写协议。
- F-10 完成、Registry 正式交付且项目负责人/MCU 负责人确认实施后才能开始。
- 完成后解除 V-03 的真机基础门，并为 H-06 提供 MCU 强制真实证据。

## 排除范围

- 香橙派 Python、OneNet 或后端实现。
- 使用 MCU Stub 冒充真机验收。
- 在生产保留旧 D1 或临时 AA/BB/CC/DD 回退解析器。
- MCU 重启后自动重放旧开门，或由香橙派猜测缺失物理事实。

## 权威来源

- [第 03 章：UART Registry、MCU 交付和恢复](../../detailed-design/03-reliable-edge-contracts.md)
- [第 05 章：清运、检测和安全恢复](../../detailed-design/05-cleaning-fullness-recovery.md)
- [第 08 章：H-03](../../detailed-design/08-implementation-sequence.md)
- [I-046～I-050：UART 1.0](../../interface-design/10-uart-protocol-i046-i050.md)
- [当前 UART 审计](../../../../hardware/docs/review/uart-protocol-audit.md)

## 进展记录

- 2026-07-23：正式任务发布；等待 F-10 Registry，保持 `blocked`；尚未授权固件实施。
- 2026-07-24：同步投递本地继续和清运电子锁真实能力；阻塞与授权状态不变。
- 2026-07-24：MCU 负责人已接受 Registry，但该确认不等于 H-03 获得实施授权，也不代替
  F-10 的 Java/Python/C 黄金样本证据；任务继续保持 `blocked`。
- 2026-07-25：香橙派 `/dev/ttyS5` 已确认可按 115200 8N1 打开，但真实 MCU 在 3 秒内
  没有任何 UART 1.0 `HELLO` 响应；未执行固件修改。H-03 仍等待 MCU 供电/接线/固件
  和实际 MCU C 工具链证据。
- 2026-07-25：为 F-11 联调实施并烧录了 `1.0.0-hil.3` 挥发 HIL 切片，真实板完成
  `0x300` 能力的一投口 HELLO、配置/重复配置和 QUERY_STATE。该切片不含持久 boot
  counter、配置/事件/危险命令非易失、完整 `0x1fff` 状态机或故障注入，也未在 MCU
  实际工具链运行生成的 C 黄金程序，因此不视为 H-03 获得全量实施授权或通过验收。
  证据见 [UART 1.0 真机 HIL 记录](../../../../hardware/docs/review/uart-hil-2026-07-25.md)。
