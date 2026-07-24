---
task_id: F-11
title: 香橙派 SQLite、OneNet、COS 与 UART 基础
status: blocked
executor: agent
owner: "TBD / edge-iot-owner"
effort_range: "8-13 person-days"
earliest_start: "F-10 done 后"
blocked_by:
  - F-10
implementation_authorized: false
---

# F-11｜香橙派 SQLite、OneNet、COS 与 UART 基础

> `status: blocked` 表示任务依赖尚未完成；`implementation_authorized: false`
> 表示本文件的发布不构成编码授权。

## 目标

在 Python 3.11 上建立香橙派可靠运行基础，以 SQLite 作为唯一恢复真相，贯通命令持久受理、
整机作业槽、UART 严格链、OneNet QoS 1、COS 照片队列、业务确认和安全启动恢复。

## 要构建什么

- 建立 `EdgeStore` 和 SQLite schema/version/integrity 管理。
- 实现 command inbox、统一 work slot、投递/清运上下文、MCU 命令与事件、event/photo
  outbox、confirmation inbox、tombstone 和 fault 表族。
- 实现五个不可拆本地事务：收命令、收 MCU 事件、建边缘事件、收业务确认和登记照片。
- 实现持久 MQTT 会话、QoS 1、SQLite 驱动重发和稳定规范 JSON。
- 实现 UART HELLO、严格解析、停等发送、ACK/NACK、事件持久化和完整 QUERY_STATE 组装。
- 实现持久照片文件、原子登记、COS 临时授权上传、补授权、永久缺失和确认后清理。
- 实现启动恢复顺序、SQLite/MCU 双事实对照、投递门 `SAFE_CLOSE`、清运人工恢复和 `SAFETY_LOCKED`。
- 使用 MCU Stub、进程强杀和网络故障注入验证基础可靠性。

## 验收条件

- [ ] SQLite 使用受控持久目录、WAL、外键、busy timeout、`synchronous=FULL` 和短写事务。
- [ ] 强杀进程后命令、作业槽、序号、事件、照片和确认不丢失。
- [ ] MQTT 回调只完成校验和持久受理，不同步等待门、重量、相机或 COS。
- [ ] MQTT PUBACK 不删除事件；只有稳定业务确认使事件和作业上下文进入清理资格。
- [ ] COS 临时凭证不进入 SQLite、文件、摘要或日志。
- [ ] UART 严格按唯一 Registry 解析并保持 CRC、ACK/NACK、命令和事件幂等。
- [ ] 启动先校验 SQLite、HELLO 和完整 QUERY_STATE，再决定恢复，不自动重放旧开门命令。
- [ ] 一次投递 session 的中间本地轮次不进入 event/photo outbox，结束或选择超时只产生一个完成事件和四个整场照片槽。
- [ ] 清运门只保存电磁阀通断推定状态和人工关门确认；断电或重启不自动宣称清运门已关闭。
- [ ] SQLite 损坏、部署身份不一致、MCU 作业冲突或快照不完整进入安全锁。
- [ ] MCU Stub 证据只关闭软件部分，不冒充 H-03 或真实设备验收。
- [ ] 旧 D1、AA/BB/CC/DD、QoS 0、内存作业和 `/tmp` 照片不在正式路径。

## 阻塞与最早开始

- 当前被 [F-10](f-10-onenet-schema-uart-registry.md) 阻塞。
- F-10 必须完成机器契约及 MCU 人工 checkpoint，F-11 才能以唯一数值和消息语义实现。

## 排除范围

- MCU 固件实现。
- V-03 的真实设备配置激活。
- V-04/V-06 的完整投递与恢复业务。
- V-07/V-08 的完整清运、满溢和基准业务。
- 后端订单、审核、钱包和资金业务。
- 用 MCU Stub 代替真实 HIL。

## 权威来源

- [详细设计 03：可靠任务与边缘契约](../../detailed-design/03-reliable-edge-contracts.md)
- [I-041～I-045：OneNet、COS 与边缘确认](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)
- [I-046～I-050：UART 1.0](../../interface-design/10-uart-protocol-i046-i050.md)
- [详细设计 08：实施任务依赖图](../../detailed-design/08-implementation-sequence.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-24：同步本地继续投递和清运电子锁恢复事实；依赖与授权状态不变。
