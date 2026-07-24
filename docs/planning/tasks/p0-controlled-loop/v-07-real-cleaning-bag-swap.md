---
task_id: V-07
title: 一次真实清运完成换袋
status: blocked
executor: mixed
owner: "待指派 - 清运端到端切片负责人"
effort_range: "9-15 person-days"
earliest_start:
  software: "V-02 与 V-03 的 software 阶段完成，清运身份、设备端口和边缘 Stub 可用"
  integration: "V-02 acceptance 完成，V-03 integration 完成，真实清运设备和 UART 固件可用"
  acceptance: "V-02、V-03 均 done，integration 完成并可执行受控真实换袋"
phase_progress:
  software: not-started
  integration: not-started
  acceptance: not-started
blocked_by:
  - V-02
  - V-03
implementation_authorized: false
---

# V-07｜一次真实清运完成换袋

## 目标

让本机构清运员通过真实设备安全完成一次可恢复换袋，使后台得到唯一清运记录、袋追溯、
新重量基准和清运后检测 gate，同时保证首次可能解锁后的不可逆边界、人工关门确认和
设备故障恢复安全。

## 要构建什么

实现清运员扫码设备、选择投口和扫描新袋；建立可恢复清运操作、新袋预留和整机占位；
在香橙派可靠保存上下文和真实解锁前稳定重量后允许电磁阀通电；支持 MCU 屏幕在原操作
内再次解锁、清运员人工关门确认和超时后的原人恢复；通过单一 `CLEAN_COMPLETE` 事件
原子建立清运记录、袋交换、基准、照片槽、检测 gate 和业务确认。

recycling 拥有清运操作、记录、袋、基准和检测事实；device 只经公开端口拥有设备占位、命令和物理事实。事务协调不能转移表所有权。

## 验收标准

- [ ] 清运员必须是当前机构普通用户附加清运能力；管理小程序工作人员身份不能替代清运主体。
- [ ] 创建操作时原子冻结旧袋/基准快照、预留新袋、建立操作和取得整机 CLEAN 占位。
- [ ] 旧袋缺失只产生警告，不阻止清运，也不伪造旧袋身份或事件。
- [ ] 袋只具有不可变身份、当前位置和追加历史，没有“可用/使用中/已清空/报废”等生命周期状态。
- [ ] 开门前重量必须来自本操作的真实稳定采样并已持久 ACK；最近心跳值和失败时的 0 均不可使用。
- [ ] 第一次解锁命令可能执行后不能普通取消或恢复旧袋；系统不声称能够检测清运门实际开关。
- [ ] 多次再次解锁不创建第二操作或记录，不改变首次解锁前重量和新袋预留。
- [ ] 完成必须同时具备清运员人工关门确认、电磁阀断电推定状态，以及最终稳定重量或明确终态称重故障；故障时不建立有效基准并保持投口阻断，断电本身不能自动完成。
- [ ] 解锁可能发生后超时或重启保留原操作/投口/袋预留并等待原清运员恢复；未现场确认关门前保持整机占位，确认锁已断电且门扇已人工关闭后可释放整机占位，恢复时重新取得；`SAFE_CLOSE` 不适用于清运门。
- [ ] 只有原清运员能恢复原操作，恢复不重新扫码、不创建新操作。
- [ ] 重复 `CLEAN_COMPLETE` 只形成一条清运记录、一次袋交换、一代基准、四个照片槽、一个检测 gate 和确认任务。
- [ ] 记录、旧袋移除、新袋安装、基准处理、检测 gate 和 device physical result 通过公开端口在同一权威事务收敛，各模块只写自己的表。
- [ ] 新基准无效时保存原值并置 INVALID，绝不沿用旧袋基准或以 0 开放投递。
- [ ] 清运审核只影响统计认定，不修改钱包、袋交换、基准和物理事实。
- [ ] 同一实体袋在本机构反复利用可形成连续位置历史。
- [ ] 真机验收覆盖正常换袋、旧袋缺失、再次解锁、人工关门确认、断网完成、超时恢复和重复事件。

## 阻塞与最早开始

| 阶段 | 最早开始条件 |
|---|---|
| software | V-02 与 V-03 的 software 阶段完成，清运身份、设备端口和边缘 Stub 可用 |
| integration | V-02 acceptance 完成，V-03 integration 完成，真实清运设备和 UART 固件可用 |
| acceptance | V-02、V-03 均完成，integration 通过并可执行受控真实换袋 |

V-02、V-03 是任务整体完成门。真机换袋、解锁/人工确认和恢复证据未齐全时不能标记
`done`。发布本任务不代表已经授权修改代码或驱动设备动作。

## 排除范围

- 由其他清运员接管恢复操作；
- 清运门首次可能解锁后的普通取消；
- 小程序远程任意开门；
- 袋的生命周期状态或跨机构复用；
- 根据旧基准反推缺失旧袋；
- 清运审核后的再次纠错；
- 设备调拨。

## 权威来源

- [清运详细设计第 05 章](../../detailed-design/05-cleaning-fullness-recovery.md)
- [可靠边缘第 03 章](../../detailed-design/03-reliable-edge-contracts.md)
- [实施依赖第 08 章](../../detailed-design/08-implementation-sequence.md)
- [I-026～I-030](../../interface-design/06-cleaning-bags-fullness-recovery-i026-i030.md)
- [I-041～I-045](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)
- [I-046～I-050](../../interface-design/10-uart-protocol-i046-i050.md)
- [I-051～I-055](../../interface-design/11-module-ports-machine-contracts-i051-i055.md)
- [D-016～D-020](../../database-design/04-recycling-d016-d020.md)
- [D-036～D-040](../../database-design/08-transactions-concurrency-d036-d040.md)

## 进展记录

- 2026-07-23：发布任务文件；仅完成设计与任务拆分，尚未授权实施。
- 2026-07-24：同步电磁阀解锁、无门磁、人工关门确认和再次解锁语义；依赖及授权状态不变。
