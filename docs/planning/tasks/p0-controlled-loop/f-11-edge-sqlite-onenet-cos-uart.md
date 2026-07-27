---
task_id: F-11
title: 香橙派 SQLite、OneNet、COS 与 UART 基础
status: in-progress
executor: agent
owner: "TBD / edge-iot-owner"
effort_range: "8-13 person-days"
earliest_start: "F-10 完成且获得显式授权后"
blocked_by:
  - F-10
implementation_authorized: true
---

# F-11｜香橙派 SQLite、OneNet、COS 与 UART 基础

> `status: in-progress`：F-10 已完成，且项目负责人已授权实施。香橙派对 OneNet 和
> 后端继续使用冻结的规范契约；现有单片机因修改成本采用双方已确定的固定帧协议，
> 由香橙派显式适配，不要求单片机原生实现 UART 1.0。

## 当前实施状态

- SQLite v2 已实现命令幂等受理、执行 claim/recovery、配置状态、可靠事件和 MCU 事件
  先持久化后 ACK；MQTT 回调只做校验、落库和服务受理响应。
- `APPLY_CONFIGURATION` 已贯通 OneNet wire 还原、Schema/摘要/身份校验、SQLite
  恢复点、UART `CONFIG_BEGIN -> DEVICE -> PORT... -> COMMIT` 严格停等和独立
  `CONFIG_APPLY_RESULT` 绑定；超时进入 `RECOVERY_REQUIRED`，不虚报失败或成功。
- UART 已按 Registry 修复发送/接收角色、ACK/NACK 匹配、同帧原字节重试、事件与 ACK
  并发路由；启动 `QUERY_STATE` 分段先持久化后 ACK，缺段或查询失败进入安全锁。
- 本地硬件测试为 70 项通过、5 个 subtests 通过；香橙派 Python 3.11 为 53 项通过，
  启动、MQTT、命令消费者和 `Ctrl+C`/`SIGINT` 停机均已验证。
- 当前服务保持 MCU Stub：真实 `/dev/ttyS5` 可打开，但 MCU 没有回应 UART 1.0
  `HELLO`。其他业务命令状态机、COS 完整闭环、强杀/断网故障注入和真实 MCU HIL
  仍未完成，不能把当前结果描述为 F-11 全部闭环。
- 2026-07-27 起，独立 worktree
  `database-refactor-f11-give-up-mcu-and-adapte-mcu` 正在实现固定帧适配器：
  `AA/BB/EE` 下行与 `DD/EF` 完成上报映射保持在硬件侧，不改变 OneNet、后端或业务
  事件结构；当前 67 项 Python 3.11 定向回归通过，但代码尚未合入本任务分支。

## 目标

在 Python 3.11 上建立香橙派可靠运行基础，以 SQLite 作为唯一恢复真相，贯通命令持久受理、
整机作业槽、UART 严格链、OneNet QoS 1、COS 照片队列、业务确认和安全启动恢复。

## 要构建什么

- 建立 `EdgeStore` 和 SQLite schema/version/integrity 管理。
- 实现 command inbox、统一 work slot、投递/清运上下文、MCU 命令与事件、event/photo
  outbox、confirmation inbox、tombstone 和 fault 表族。
- 实现五个不可拆本地事务：收命令、收 MCU 事件、建边缘事件、收业务确认和登记照片。
- 实现持久 MQTT 会话、QoS 1、SQLite 驱动重发和稳定规范 JSON。
- 以显式协议模式实现 MCU 适配：`uart-v1` 使用 HELLO/ACK/NACK/QUERY_STATE；
  `fixed-frame` 使用
  [`ecobin-mcu-fixed-frame-v1`](../../../../contracts/mcu-fixed-frame-v1.md)
  并对缺失能力明确拒绝或上报未知，不自动探测或失败回退。
- 实现持久照片文件、原子登记、COS 临时授权上传、补授权、永久缺失和确认后清理。
- 实现启动恢复顺序、SQLite/MCU 双事实对照、投递门 `SAFE_CLOSE`、清运人工恢复和 `SAFETY_LOCKED`。
- 使用 MCU Stub、进程强杀和网络故障注入验证基础可靠性。

## 验收条件

- [ ] SQLite 使用受控持久目录、WAL、外键、busy timeout、`synchronous=FULL` 和短写事务。
- [ ] 强杀进程后命令、作业槽、序号、事件、照片和确认不丢失。
- [ ] MQTT 回调只完成校验和持久受理，不同步等待门、重量、相机或 COS。
- [ ] MQTT PUBACK 不删除事件；只有稳定业务确认使事件和作业上下文进入清理资格。
- [ ] COS 临时凭证不进入 SQLite、文件、摘要或日志。
- [ ] OneNet/边缘规范事实严格按 F-10 机器契约处理；固定帧差异只存在于显式 MCU
  适配器，不泄漏为第二套云端业务协议。
- [ ] `uart-v1` 启动校验 HELLO/QUERY_STATE；`fixed-frame` 因无协议级状态查询而不重放
  旧物理命令，并把未决工作转为明确失败/人工恢复，不能伪造 MCU 快照。
- [ ] 一次投递 session 的中间本地轮次不进入 event/photo outbox，结束或选择超时只产生一个完成事件和四个整场照片槽。
- [ ] 清运门分别保存电磁阀通断和人工关门确认，物理门位保持 `UNKNOWN`；断电或重启
  不自动宣称清运门已关闭。
- [ ] SQLite 损坏、部署身份不一致、MCU 作业冲突或快照不完整进入安全锁。
- [ ] MCU Stub 和固定帧软件测试只关闭软件部分，不冒充 H-03 真实设备验收。
- [ ] 旧 D1、QoS 0、内存作业和 `/tmp` 照片不在正式路径；固定帧模式只能显式配置，
  不与 `uart-v1` 自动双解析或失败回退。

## 阻塞与最早开始

- [F-10](f-10-onenet-schema-uart-registry.md) 已完成，项目负责人已明确授权 F-11
  实施，因此当前状态为 `in-progress`。
- 当前完成门是本任务自身的 SQLite/COS/命令状态机/故障注入和固定帧适配软件验收。
  H-03 是下游真实设备门，不反向阻塞 F-11 软件任务完成；F-11 `done` 也不得冒充
  H-03 真机联调通过。

## 排除范围

- MCU 固件实现。
- V-03 的真实设备配置激活。
- V-04/V-06 的完整投递与恢复业务。
- V-07/V-08 的完整清运、满溢和基准业务。
- 后端订单、审核、钱包和资金业务。
- 用 MCU Stub 代替真实 HIL。
- 把固定帧协议缺少的 ACK、CRC、流程身份、状态查询或故障枚举伪造成 MCU 已提供。

## 权威来源

- [详细设计 03：可靠任务与边缘契约](../../detailed-design/03-reliable-edge-contracts.md)
- [I-041～I-045：OneNet、COS 与边缘确认](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)
- [I-046～I-050：UART 1.0](../../interface-design/10-uart-protocol-i046-i050.md)
- [详细设计 08：实施任务依赖图](../../detailed-design/08-implementation-sequence.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-24：同步本地继续投递和清运电子锁恢复事实；依赖与授权状态不变。
- 2026-07-24：F-10 软件机器来源完成且 MCU 负责人接受 Registry；F-11 取得可实施的
  唯一数值基线。
- 2026-07-24：项目负责人授权 F-11 实施；任务进入 `in-progress`。初步代码骨架已经
  形成，但主审确认 MQTT、UART 事件持久 ACK、恢复、COS 和测试证据仍有阻断问题，
  尚未达到 `in-review`。
- 2026-07-25：完成 OneNet 命令可靠受理和 `APPLY_CONFIGURATION` 配置纵切，补齐
  SQLite v2 恢复点、UART 分段停等、MCU 事件先持久化后 ACK、结果强绑定与超时恢复。
  本地和香橙派测试全部通过，远端服务以 `ECOBIN_TEST_MODE=true` 恢复为 `READY`；
  真实 MCU `HELLO` 无响应，因此真机配置激活和其余命令族仍待继续。
- 2026-07-27：项目负责人确认现有单片机不再原生实现 UART 1.0，F-10 转为 `done`；
  F-11 改为在香橙派侧显式选择固定帧适配器。适配 worktree 已完成协议解析、投递/清运
  完成事件映射和 67 项 Python 3.11 定向回归，仍待整理提交、合入及真实设备验收。
