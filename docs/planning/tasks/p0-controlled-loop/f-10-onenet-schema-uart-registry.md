---
task_id: F-10
title: OneNet Schema 与 UART Registry 冻结
status: blocked
executor: mixed
owner: "TBD / cross-end-contract-owner"
effort_range: "3-5 person-days (software 2-3; integration 0.5-1; acceptance 0.5-1)"
earliest_start:
  software: "正式实施获授权后立即"
  integration: "机器草案完成且 MCU 负责人进入数值与能力审查后"
  acceptance: "消息号、字段偏移、能力位和非易失边界确认后"
blocked_by: []
phase_progress:
  software: done
  integration: in-progress
  acceptance: in-progress
implementation_authorized: true
---

# F-10｜OneNet Schema 与 UART Registry 冻结

> 当前状态：**软件、Registry checkpoint、通用三语言黄金样本和 `0x300` 真机 HIL
> 纵切已完成，等待 MCU 实际工具链黄金程序证据收口**。
>
> `status: blocked`：MCU 负责人已经接受 UART Registry 数值、状态机边界、能力位与
> 非易失能力；Java 21、真实 Python 3.11 和香橙派 GCC 12.2 的 C11 黄金样本也已通过。
> 真机已完成 HELLO、配置、重复配置和状态查询纵切，但仍缺 MCU 实际 C 工具链对生成
> 黄金程序的执行证据，也未覆盖完整能力与持久恢复，不能据此把整个 Mixed 任务标记
> `done`。

## 目标

形成 OneNet 与 UART 的唯一机器来源，使后端、香橙派和 MCU 使用同一身份、单位、数值、
状态和黄金样本；由 MCU 负责人确认线级数值与非易失能力后，才能冻结契约并完成任务。

## 要构建什么

- 建立机器可读 OneNet 命令、事件、接收结果、业务确认和确认回执 Schema。
- 建立 UART 1.0 Registry，定义帧头、长度、大小端、CRC、消息号、字段偏移、能力位和错误码。
- 定义 UUID、带符号整数克、定点单价、时间单位和规范摘要。
- 覆盖 HELLO、ACK/NACK、QUERY_STATE、配置、投递 session、本地继续、清运电磁阀解锁/
  人工关门确认、满溢和基准测量消息族；`SAFE_CLOSE` 只覆盖可自动关闭的投递门。
- 将六投口一致快照定义为 `STATE_SNAPSHOT_BEGIN/PORT/END` 应用级分段。
- 形成 Java 21、Python 3.11 和 C 可使用的生成物或校验器及黄金向量。
- 组织 MCU 负责人确认消息数值、状态机边界、能力位和关键事件非易失能力，并记录结论。

## 验收条件

- [x] OneNet Schema 明确区分传输接受、香橙派可靠受理、物理动作和后端业务完成。
- [x] UART Registry 明确帧格式、长度、big-endian、CRC-16/CCITT-FALSE 和稳定消息身份。
- [x] Registry 覆盖配置、投递 session/最终完成、清运解锁/人工确认、检测、测量、投递门 `SAFE_CLOSE` 和状态查询。
- [x] 投递中间轮次不上 OneNet；最终 `negativeWeightAnomaly` 只随完成载荷出现且不携中间减少值。
- [x] 清运电磁阀通断仅推定门状态，Registry 不定义清运门门磁或自动关门能力。
- [x] 六投口快照使用 BEGIN/PORT/END，不能用部分快照解除安全锁。
- [ ] Java 21、Python 3.11、C 对同一黄金样本编码和校验一致（前三者已在通用工具链
  通过，仍缺 MCU 实际 C 工具链证据）。
- [x] MCU 负责人确认消息号、字段偏移、能力位、错误码、状态机和非易失边界。
- [x] 旧 D1 与临时 AA/BB/CC/DD 不在正式 Registry。
- [x] Mixed 任务只有全部 integration/acceptance 证据齐全后才能标记 `done`；当前按此
  规则保持 `blocked`。

Java 黄金样本、真实 Python 3.11 生成物与测试、香橙派 GCC 12.2 的 C11 黄金程序均已
通过；生成器中重复定义 `ecobin_uart_sender_role_t` 的问题也已修复，C 编译使用
`-Wall -Wextra -Werror` 零告警。真实 MCU 的 `0x300` 配置/快照 HIL 已通过，但它没有
在 MCU 实际 ARMCC/Keil 工具链中单独执行生成的黄金程序，也不覆盖完整能力与非易失
边界，因此 integration/acceptance 尚未完全收口。

## 阻塞与最早开始

- 软件阶段无任务依赖，可在获得正式实施授权后开始。
- integration 阶段的 Registry 审查、通用三语言黄金样本和局部真机 UART HIL 已经
  完成，仍需补齐 MCU 实际 C 工具链黄金程序证据。
- acceptance 阶段已经取得 MCU 负责人对 Registry 的明确确认，但仍须证明三端数值和
  黄金样本一致。
- 外部排队等待不计入 `effort_range`。

## 排除范围

- MCU 固件实现和 H-03 真机验收。
- 香橙派完整运行时；它属于 F-11。
- 投递、清运、配置和满溢业务纵向状态机。
- 生产双协议解析器或旧协议失败回退。
- 首个共同版本前的完整自动 CI/HIL 发布门禁。

## 权威来源

- [详细设计 03：可靠任务与边缘契约](../../detailed-design/03-reliable-edge-contracts.md)
- [I-041～I-045：OneNet、COS 与边缘确认](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)
- [I-046～I-050：UART 1.0](../../interface-design/10-uart-protocol-i046-i050.md)
- [I-051～I-055：模块端口与机器契约](../../interface-design/11-module-ports-machine-contracts-i051-i055.md)
- [详细设计 08：实施任务依赖图](../../detailed-design/08-implementation-sequence.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；软件阶段依赖已满足，尚未授权实施，人工
  checkpoint 尚未开始。
- 2026-07-24：同步 session 一单和清运电子锁边界；软件 `ready`、人工 checkpoint 与实施授权状态不变。
- 2026-07-24：软件阶段完成。OneNet 候选物模型已由项目负责人确认可正常导入控制台；
  生成物检查、Schema/Registry 校验、Java 黄金样本和 20 项工具测试通过。任务进入
  "软件已完成、等待 MCU 与联调验收收口"，真实 Python 3.11、MCU C 工具链、逐字段
  checkpoint 和跨端真机证据仍未完成，因此任务级状态保持 `blocked`。
- 2026-07-24：MCU 负责人已接受 UART Registry 与消息数值、状态机边界、能力位和非易失
  能力，人工 Registry checkpoint 完成。主审复核发现 Java/Python/C 同一黄金样本证据
  仍未齐全，因此任务整体保持 `blocked`，integration/acceptance 继续
  `in-progress`。
- 2026-07-25：真实 Python 3.11 契约测试通过；修复 C 生成器的 SenderRole 重复类型
  缺陷后，香橙派 GCC 12.2 以 C11、`-Wall -Wextra -Werror` 编译并运行黄金程序通过
  （10 帧、10 条流轨迹、3 个摘要）。`/dev/ttyS5` 可打开但 MCU 在 3 秒内没有
  `HELLO` 响应，仍缺 MCU 实际工具链与 HIL 证据，任务保持 `blocked`。
- 2026-07-25：烧录 `1.0.0-hil.3` 并复位后，真实 MCU 在 `/dev/ttyS5` 完成 HELLO、
  配置分段、配置结果、重复 COMMIT 去重和严格 QUERY_STATE 快照校验；能力范围为
  `0x300`、一投口。该证据见
  [UART 1.0 真机 HIL 记录](../../../../hardware/docs/review/uart-hil-2026-07-25.md)。
  MCU 实际 ARMCC/Keil 尚未单独运行生成的 C 黄金程序，任务继续保持 `blocked`。
