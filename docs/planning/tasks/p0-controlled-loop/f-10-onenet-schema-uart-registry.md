---
task_id: F-10
title: OneNet Schema 与 UART Registry 冻结
status: done
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
  integration: done
  acceptance: done
implementation_authorized: true
---

# F-10｜OneNet Schema 与 UART Registry 冻结

> 当前状态：**OneNet Schema、UART Registry、生成物、通用三语言黄金样本和契约
> checkpoint 均已完成，任务已收口**。
>
> `status: done`：Java 21、真实 Python 3.11 和香橙派 GCC 12.2 的 C11 黄金样本已经
> 通过。2026-07-27 项目负责人确认三端业务协议已经确定，现有单片机不再以原生实现
> UART 1.0、运行生成 C 黄金程序或通过该协议 HIL 作为 F-10 完成条件；线路差异由
> F-11 的显式固定帧适配层承担，真机验收归 H-03。

## 目标

形成 OneNet 与边缘规范 UART 模型的唯一机器来源，使后端和香橙派使用同一身份、单位、
数值、状态和黄金样本，并为能够原生实现 UART 1.0 的 MCU 提供完整参考。现有固定帧
单片机通过 F-11 适配层映射到该规范模型，不反向修改 OneNet 或后端业务契约。

## 要构建什么

- 建立机器可读 OneNet 命令、事件、接收结果、业务确认和确认回执 Schema。
- 建立 UART 1.0 Registry，定义帧头、长度、大小端、CRC、消息号、字段偏移、能力位和错误码。
- 定义 UUID、带符号整数克、定点单价、时间单位和规范摘要。
- 覆盖 HELLO、ACK/NACK、QUERY_STATE、配置、投递 session、本地继续、清运电磁阀解锁/
  人工关门确认、满溢和基准测量消息族；`SAFE_CLOSE` 只覆盖可自动关闭的投递门。
- 将六投口一致快照定义为 `STATE_SNAPSHOT_BEGIN/PORT/END` 应用级分段。
- 形成 Java 21、Python 3.11 和 C 可使用的生成物或校验器及黄金向量。
- 记录消息数值、状态机边界、能力位和可靠性假设；具体单片机不具备的能力由适配层显式
  降级或拒绝，不能伪造为已实现。

## 验收条件

- [x] OneNet Schema 明确区分传输接受、香橙派可靠受理、物理动作和后端业务完成。
- [x] UART Registry 明确帧格式、长度、big-endian、CRC-16/CCITT-FALSE 和稳定消息身份。
- [x] Registry 覆盖配置、投递 session/最终完成、清运解锁/人工确认、检测、测量、投递门 `SAFE_CLOSE` 和状态查询。
- [x] 投递中间轮次不上 OneNet；最终 `negativeWeightAnomaly` 只随完成载荷出现且不携中间减少值。
- [x] 清运电磁阀通断不能推定物理门位；Registry 以 `NOT_OBSERVABLE` /
  `CLEANER_CONFIRMATION` 分型，不定义清运门门磁或自动关门能力。
- [x] 六投口快照使用 BEGIN/PORT/END，不能用部分快照解除安全锁。
- [x] Java 21、Python 3.11、C 对同一黄金样本编码和校验一致。
- [x] 消息号、字段偏移、能力位、错误码、状态机和可靠性边界已经记录；现有固定帧
  单片机无需原生实现 Registry，由 F-11 适配层负责映射。
- [x] 旧 D1 与临时 AA/BB/CC/DD 不在正式 Registry。
- [x] OneNet/后端契约不因单片机固定帧适配而产生第二套业务协议或自动失败回退。

Java 黄金样本、真实 Python 3.11 生成物与测试、香橙派 GCC 12.2 的 C11 黄金程序均已
通过；生成器中重复定义 `ecobin_uart_sender_role_t` 的问题也已修复，C 编译使用
`-Wall -Wextra -Werror` 零告警。MCU 实际 C 工具链与真机协议符合性不再是本任务
门槛：选择 `uart-v1` 时由 H-03 验收原生符合性；当前固定帧路线则由 F-11/H-03 验证
适配映射和真实设备行为。

## 阻塞与最早开始

- 软件阶段无任务依赖，可在获得正式实施授权后开始。
- integration 阶段的 Registry 审查和通用三语言黄金样本已经完成。
- acceptance 阶段以项目负责人确认三端业务协议及适配责任边界收口；不再等待现有
  单片机原生实现 UART 1.0。
- 外部排队等待不计入 `effort_range`。

## 排除范围

- MCU 固件实现和 H-03 真机验收。
- 香橙派完整运行时；它属于 F-11。
- 现有单片机固定帧解析、业务事实映射和兼容模式恢复策略；它们属于 F-11。
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
- 2026-07-27：项目负责人确认三端业务协议已经确定，现有单片机因修改成本改由 F-11
  在香橙派侧建立显式固定帧适配层；MCU 原生 UART 1.0、目标工具链黄金程序和该协议
  HIL 不再作为 F-10 完成门。在 F-11 固定帧适配 worktree 复核确认 68 个生成物
  无漂移、15 项契约校验、31 项契约单测及固定帧适配层 67 项 Python 3.11 定向回归
  通过，F-10 的 software、
  integration、acceptance 全部转为 `done`。
- 2026-07-28：按当前固定帧责任边界，默认生成/检查改为 66 个仓库和香橙派制品，
  `hardware_mcu/` 输出仅在显式 `--include-hardware-mcu` 时生成。当前为 17 项契约
  校验、`43 passed, 64 subtests passed`；UART 黄金样本更新为 11 个帧向量、10 条
  流轨迹和 3 个摘要。上述更新不重新打开已完成的 F-10。
