---
task_id: H-03
title: 固定帧 MCU 线路与真机基础验收
status: blocked
executor: human
owner: "unassigned — hardware/HIL acceptance owner"
effort_range: "2-5 person-days"
earliest_start: "after F-11 is done and real hardware acceptance is explicitly authorized"
blocked_by:
  - F-11
implementation_authorized: false
---

# H-03｜固定帧 MCU 线路与真机基础验收

> `status: blocked`：2026-07-27 项目负责人确认现有单片机不再原生实现 UART 1.0，
> 香橙派通过 F-11 固定帧适配层接入。H-03 保留真实硬件强制证据职责，但须等待 F-11
> 适配实现完成并获得现场验收授权。

## 目标

在不要求修改现有单片机固件架构的前提下，验证协商后的 `115200 / 8N1` 固定帧协议、
MCU 屏幕状态机、称重、投递门机构和清运电磁阀在真实香橙派与设备上的行为；明确协议无法
表达的恢复和安全事实，禁止适配层伪造。

## 要执行什么

- 核对并抓取 `BB PRICE BB`、`AA 01 AA`、`EE 01 EE`、`DD PRE POST FULL DD` 和
  `EF PRE POST FULL EF` 的真实原始字节。
- 验证半帧、粘包、前导噪声和载荷内出现帧标志时，香橙派仍按固定长度正确解析。
- 验证一次投递只发送一次开始命令；MCU 保留整场首次重量，用户继续投递不上报，结束时
  只发送一次 DD。
- 验证一次清运只发送一次开始命令；电磁阀在 3～5 秒后自动断电，工作人员换袋、关门并
  在屏幕确认后才发送一次 EF。
- 对照真实秤值验证 uint24 大端克数、`FULL=00/01` 和香橙派生成的规范测量/业务事件。
- 执行香橙派重启、单片机重启、迟到 DD/EF、无活动工作时收到结果、重复结果和串口断开
  场景，确认不会自动重放物理命令或把未知事实冒充为成功。
- 记录单投口、无 ACK/CRC/流程编号/状态查询/故障枚举等已知限制，并确认试点启用范围。

## 验收与证据

- [ ] 真实串口参数和五类固定帧与
  [`ecobin-mcu-fixed-frame-v1` 1.0.0](../../../../contracts/mcu-fixed-frame-v1.md)
  逐字节一致。
- [ ] DD/EF 的前后重量、满溢原始观测和对应活动工作绑定正确。
- [ ] 投递继续轮次不上云；最终只形成一个 `DELIVERY_COMPLETE`。
- [ ] EF 只在继电器断电和工作人员关门确认后出现；适配层不宣称未知的电磁阀健康。
- [ ] 单片机或香橙派重启后不会自动重放旧投递/清运命令。
- [ ] 迟到、重复或无活动工作的 DD/EF 不会生成错误归属的业务完成事件。
- [ ] 固定帧无法证明的状态保持 `UNKNOWN/NOT_SAMPLED` 或明确不支持，不使用 0 或成功占位。
- [ ] 真机证据包含固件版本、接线、串口抓包、秤值对照、屏幕操作和异常场景结果。
- [ ] 已知限制与试点启用范围经项目负责人确认，不把软件回归冒充 HIL。

## 阻塞与最早开始

- 被 [F-11](f-11-edge-sqlite-onenet-cos-uart.md) 阻塞：必须先完成固定帧适配实现，
  再使用同一版本做真机验收。
- F-10 已完成，不再要求现有单片机运行 UART 1.0 生成 C 黄金程序。
- 完成后解除 V-03 的真机基础门，并为 H-06 提供真实设备证据。

## 排除范围

- 强制现有单片机改写为 UART 1.0、实现 Registry 全消息族或非易失队列。
- 香橙派 OneNet、COS、SQLite 业务实现；它属于 F-11。
- 使用 MCU Stub 或固定帧单元测试冒充真机验收。
- 自动探测两种协议、失败后回退或同时运行双解析器。
- 从固定帧缺失字段推断门磁、传感器健康、MCU boot 或命令幂等事实。

## 权威来源

- [`ecobin-mcu-fixed-frame-v1` 1.0.0](../../../../contracts/mcu-fixed-frame-v1.md)
- [F-10 机器契约](f-10-onenet-schema-uart-registry.md)
- [F-11 香橙派适配任务](f-11-edge-sqlite-onenet-cos-uart.md)
- [第 03 章：可靠边缘契约](../../detailed-design/03-reliable-edge-contracts.md)
- [第 05 章：清运、检测和安全恢复](../../detailed-design/05-cleaning-fullness-recovery.md)
- [第 08 章：H-03](../../detailed-design/08-implementation-sequence.md)
- [当前 UART 审计](../../../../hardware/docs/review/uart-protocol-audit.md)

## 进展记录

- 2026-07-23：原任务按“MCU 原生 UART 1.0 固件与真机基础验收”发布，等待 F-10，
  且未获得固件实施授权。
- 2026-07-24：MCU 负责人接受 Registry 数值，但该确认不等于固件实施或真机验收。
- 2026-07-25：香橙派 `/dev/ttyS5` 可按 115200 8N1 打开，现有 MCU 没有回应
  UART 1.0 `HELLO`。
- 2026-07-27：项目负责人确认不再以修改现有 MCU 为当前路线，改由 F-11 在香橙派侧
  建立固定帧适配层。H-03 保留原任务 ID 和真实硬件验收职责，范围改为固定帧线路、
  屏幕状态机和物理行为验收，依赖由 F-10 调整为 F-11。
