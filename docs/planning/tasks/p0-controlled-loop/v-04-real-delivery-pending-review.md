---
task_id: V-04
title: 一次真实投递形成待审核订单
status: blocked
executor: mixed
owner: "待指派 - 投递端到端切片负责人"
effort_range: "9-14 person-days"
earliest_start:
  software: "V-02 与 V-03 的 software 阶段完成，相关公开端口和 Stub 契约稳定"
  integration: "V-02 acceptance 完成，V-03 integration 完成，真实试点部署可安全作业"
  acceptance: "V-02、V-03 均 done，integration 完成并可执行受控真机开关门"
phase_progress:
  software: not-started
  integration: not-started
  acceptance: not-started
blocked_by:
  - V-02
  - V-03
implementation_authorized: false
---

# V-04｜一次真实投递形成待审核订单

## 目标

让一个已绑定手机号的机构用户通过真实设备完成一次不使用继续按钮的正常投递 session，
并由后端根据整场首末重量可靠建立唯一待审核订单；为 V-06 的本地继续保留同一 session
语义，但本任务不实现连续多轮。

## 要构建什么

实现用户扫码选投口、session 复合授权、首次开门前称重/照片、一次正常开关门、最终
称重/照片、唯一 OneNet 完成事件、待审核订单、投递结束后检测 gate、业务确认和小程序
时间线。

开始授权采用修订后的 PDD-001：由 recycling 开启外层复合事务，严格按 D-037 先取得
identity、recycling 配置和 funds 钱包锁，再调用 device 公开参与端口；device 仍是
session、occupancy、command 和 physical result 的唯一写入者。继续投递属于已授权
session 内本地动作，不建立云端 cycle 或再次调用该复合事务。

## 验收标准

- [ ] 开始授权由 recycling 外层协调；各模块只写自己的表，Maven 依赖中不出现 `device → funds` 或 `device → recycling`。
- [ ] D-037 精确锁序落实，不能把 identity 前后锁段粗化，也不能先锁 device 再反向取得钱包锁。
- [ ] 开始事务只创建 session、整机占位和以 session 为目标的命令，不提前创建订单。
- [ ] session 从完整锁根校验资格并冻结用户、机构、投口、配置、单价、袋、负重量阈值和限制。
- [ ] 香橙派持久保存授权和作业上下文，首次重量事件持久 ACK 后才开门；重量失败不能用 0。
- [ ] 同一设备的其他用户只能看到“设备使用中”，不能覆盖当前 session。
- [ ] `sessionUid` 单独约束最多一个 physical result 和订单；同一完成事件并发重投只产生一组四图、检测 gate 和确认任务，同一 session 更换 `eventUid` 再报也不得创建第二单并须进入冲突处置。
- [ ] 后端按首次开门前和最终关门后稳定总重量重算净重；本地达到默认 500g 阈值只随最终载荷上报布尔异常标志，审核前钱包不变。
- [ ] 零重量和照片缺失不标记用户投递异常；照片缺失仍可建单并进入后续返现流程。
- [ ] 订单使用 session 开始时锁定单价，继续期间改价不影响整场金额。
- [ ] 小程序能查看原始待审核事实和时间线，但待审核负金额不进入钱包。
- [ ] 只有后端业务确认持久化后，边缘才可清理原事件；MQTT PUBACK 不具备该效力。
- [ ] 真机验收覆盖一次正常投递、选择结束、零重量、负重量标志、缺图、重复事件和另一用户竞争。

## 阻塞与最早开始

| 阶段 | 最早开始条件 |
|---|---|
| software | V-02 与 V-03 的 software 阶段完成，相关公开端口和 Stub 契约稳定 |
| integration | V-02 acceptance 完成，V-03 integration 完成，真实试点部署可安全作业 |
| acceptance | V-02、V-03 均完成，integration 通过并可执行受控真机开关门 |

V-02、V-03 是任务整体完成门；先行的软件工作不得绕过 PDD-001 或以 Stub 冒充真机验收。发布本任务不代表已经授权修改代码或驱动设备动作。

## 排除范围

- 投递审核、纠错和钱包正式入账；
- 连续投递、选择超时、断网及迟到结果恢复；
- 清运换袋；
- 人工满溢重检和基准恢复；
- 自动提现；
- 将负重量自动认定为偷取或处罚用户。

## 权威来源

- [投递详细设计第 04 章](../../detailed-design/04-delivery-review-wallet.md)
- [可靠边缘第 03 章](../../detailed-design/03-reliable-edge-contracts.md)
- [实施依赖第 08 章](../../detailed-design/08-implementation-sequence.md)
- [I-021～I-025](../../interface-design/05-delivery-review-wallet-i021-i025.md)
- [I-041～I-045](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)
- [I-046～I-050](../../interface-design/10-uart-protocol-i046-i050.md)
- [I-051～I-055](../../interface-design/11-module-ports-machine-contracts-i051-i055.md)
- [D-016～D-020](../../database-design/04-recycling-d016-d020.md)
- [D-036～D-040](../../database-design/08-transactions-concurrency-d036-d040.md)

## 进展记录

- 2026-07-23：发布任务文件；仅完成设计与任务拆分，尚未授权实施。
- 2026-07-24：同步一次 session 一单、整场首末重量/四图和本地继续语义；依赖及授权状态不变。
