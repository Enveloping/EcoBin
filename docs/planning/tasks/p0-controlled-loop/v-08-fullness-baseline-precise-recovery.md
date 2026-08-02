---
task_id: V-08
title: 满溢、基准和精确安全恢复
status: blocked
executor: mixed
owner: "待指派 - 容量与安全恢复端到端切片负责人"
effort_range: "7-12 person-days"
earliest_start:
  software: "V-04 与 V-07 的 software 阶段完成，订单/清运代际和设备检测 Stub 可用"
  integration: "V-04、V-07 integration 完成，真实红外、称重、门状态和故障注入可用"
  acceptance: "V-04、V-07 均 done，integration 完成并可取得传感器与恢复真机证据"
phase_progress:
  software: in-progress
  integration: not-started
  acceptance: not-started
blocked_by:
  - V-04
  - V-07
implementation_authorized: false
---

# V-08｜满溢、基准和精确安全恢复

> 2026-08-02 裁决：正常投递准入已经改为设备状态变化被动上报，只有当前袋明确 `FULL` 才阻止下一次投递。后端主动检测、过期 `SAMPLE_FULLNESS` 重新签发和人工重检不再是完成本任务的前置条件；详见 [`../../../architecture/fullness-reporting-v25.md`](../../../architecture/fullness-reporting-v25.md)。本任务仍因真实传感器、基准重测、精确安全恢复和真机验收等剩余范围保持 `blocked`，不能据此宣称 V-08 整体完成。

## 目标

让机构能够基于最近一次真实检测判断投口满溢状态，在基准或设备事实异常时通过新的真实证据恢复精确阻断，而不是由后台直接篡改容量、皮重或设备状态。

## 要构建什么

实现整场投递结束后、清运后和人工重检三类按需满溢采样；支持红外、重量、红外或重量
三种冻结模式，容量代际、确认样本、活动满溢事件及 gate；实现满溢度计算和最近检测
展示；实现真实空袋基准重测、投递 session 结果待处理恢复和严重安全锁精确恢复。

## 验收标准

- [ ] MCU 只在整场投递 session 结束后、清运后或人工重检时进行业务满溢采样；中间本地继续轮次不采样、不阻止当前用户。
- [ ] 后端先创建稳定 `detectionUid` 和规则快照，设备不能自行创造权威检测身份。
- [ ] `INFRARED_ONLY`、`WEIGHT_ONLY`、`INFRARED_OR_WEIGHT` 的 FULL、NOT_FULL 和失败矩阵与冻结规则一致。
- [ ] 任一必需来源失败时停止该投口的下一 session；“一个来源不满、另一个失败”不能判未满。
- [ ] 满溢度使用当前总重量减当前有效基准；允许超过 100%，原始净重量为负时显示 0% 但保留原值。
- [ ] 重量、基准或阈值不可用时显示未知而不是 0，并明确标注为“上次检测值”及检测时间。
- [ ] 样本只在当前袋、基准、规则指纹和 `currentDetectionId` 全部匹配时应用；旧代际保存为 `STALE_IGNORED`。
- [ ] 可靠 NOT_FULL 可以恢复活动满溢事件；来源失败保持原阻断，不覆盖为未满。
- [ ] 人工重检只能发起真实 `SAMPLE_FULLNESS`，不能直接提交 FULL 或 NOT_FULL。
- [ ] 空袋基准重测只在基准未初始化或无效且现场确认袋空时发起；成功后仍需新的真实检测结束才能恢复投递。
- [ ] 投递结果待处理恢复要求现场确认和新真实检测，不创建、删除、审核或改挂订单。
- [ ] 严重安全锁只能凭精确 fault、期望运行版本和新鲜真实设备证据恢复；其他故障、占位、容量、订单和经营开关保持不变。
- [ ] Web 与管理小程序按平台、租户、机构作用域展示，管理小程序仅提供当前机构精简只读视图。
- [ ] 真机验收覆盖三模式、一个来源失败、垃圾下沉后重检、负净重、超过 100%、换袋代际竞争、基准失败和安全故障精确恢复。

## 阻塞与最早开始

| 阶段 | 最早开始条件 |
|---|---|
| software | V-04 与 V-07 的 software 阶段完成，订单/清运代际和设备检测 Stub 可用 |
| integration | V-04、V-07 integration 完成，真实红外、称重、门状态和故障注入可用 |
| acceptance | V-04、V-07 均完成，integration 通过并可取得传感器与恢复真机证据 |

V-04、V-07 是任务整体完成门。软件计算或 Stub 结果不能替代真实传感器和精确恢复证据。发布本任务不代表已经授权修改代码、设备配置或驱动现场动作。

## 排除范围

- 空闲期间持续满溢业务判定；
- 人工直接填写未满、皮重或设备正常；
- 清除全部故障或重置整机状态；
- 用旧传感器值或 0 兜底；
- 任意远程开门；
- 袋生命周期；
- 自动处罚负重量用户。

## 权威来源

- [清运与恢复第 05 章](../../detailed-design/05-cleaning-fullness-recovery.md)
- [可靠边缘第 03 章](../../detailed-design/03-reliable-edge-contracts.md)
- [投递第 04 章](../../detailed-design/04-delivery-review-wallet.md)
- [实施依赖第 08 章](../../detailed-design/08-implementation-sequence.md)
- [I-026～I-030](../../interface-design/06-cleaning-bags-fullness-recovery-i026-i030.md)
- [I-041～I-045](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)
- [I-046～I-050](../../interface-design/10-uart-protocol-i046-i050.md)
- [D-016～D-020](../../database-design/04-recycling-d016-d020.md)
- [D-036～D-040](../../database-design/08-transactions-concurrency-d036-d040.md)

## 进展记录

- 2026-07-23：发布任务文件；仅完成设计与任务拆分，尚未授权实施。
- 2026-07-24：同步满溢仅在整场投递结束后判断并影响下一 session；依赖及授权状态不变。
- 2026-08-02：软件侧已实施 V25 当前袋状态变化上报、后端被动接收、旧袋/乱序防护以及查询/启动统一 `FULL` 准入；V25 迁移停用旧主动检测任务。取消“过期命令重新签发/人工重检恢复”阻塞项，V-08 其余真机与精确恢复范围仍未完成。
