---
task_id: P0-CONTROLLED-LOOP-INDEX
title: EcoBin P0 受控闭环实施任务索引
status: done
executor: agent
owner: "main reviewer"
effort_range: "0.5-1 person-days"
earliest_start: "2026-07-23"
blocked_by: []
implementation_authorized: false
---

# EcoBin P0 受控闭环实施任务索引

> 这里发布的是已经批准的实施任务。项目负责人已单独授权并完成 H-01、F-01、F-02、
> F-04，并确认 F-10 软件阶段完成和 F-11 软件实施授权；其他任务仍须逐项获得授权。
> `status: ready` 只表示任务设计和前置依赖允许领取，不构成后续任务的自动授权。

## Initiative 状态

| 项目 | 当前值 |
|---|---|
| Initiative | `p0-controlled-loop` |
| 任务数 | 29（F-01～F-12、V-01～V-11、H-01～H-06） |
| 设计状态 | 详细设计、任务粒度、依赖和执行分类已批准；2026-07-24 已同步投递 session/清运电子锁修订 |
| 实施授权 | **部分授权：H-01、F-01、F-02、F-04 已授权并完成；F-10 通用三语言证据与局部 HIL 已通过但 MCU 实际工具链黄金程序未收口；F-11 已授权并实施中；其他任务未授权** |
| 当前状态数 | `done` 4、`ready` 3、`in-progress` 1、`blocked` 21 |
| 风险目标 | 2026-07-30 只用于风险排序，不构成 G1、G2 或 M0 承诺 |
| 权威依赖来源 | [第 08 章](../../detailed-design/08-implementation-sequence.md) |

## 初始工作量审查

| executor | 任务数 | 工作量 |
|---|---:|---:|
| `agent` | 15 | 67～112 person-days |
| `mixed` | 8 | 49～82 person-days |
| `human` | 6 | 10～20 person-days |
| **合计** | **29** | **126～214 person-days** |

- G1 基础关键链约为 14～24 person-days。
- 到 H-06 的最长内部依赖链约为 54～90 person-days。
- 估算不含微信能力开通、环境等待、人员排队或返工；角色 owner 不是已经指派的个人。
- 领取首批任务后要用真实吞吐重新校准。当前估算进一步证明 2026-07-30 不能作为完整 M0 承诺。

## 状态与执行主体

任务生命周期使用：

```text
needs-triage | needs-info | ready | in-progress | in-review | blocked | done | wontfix
```

执行主体使用：

```text
agent | human | mixed
```

- `status` 表示任务处于哪个生命周期阶段；`executor` 表示由谁完成，两者不能合并。
- `blocked` 表示仍有任务依赖、外部条件或安全门槛。
- `mixed` 任务的软件阶段可以先推进，但只有 integration/acceptance 的人工证据齐全后才能 `done`。
- 任务文件中的 `implementation_authorized: false` 只有在项目负责人明确授权相应范围后才能改变。

完整词汇见 [任务状态与执行主体](../../../agents/triage-labels.md)，本地任务维护规则见
[EcoBin 本地任务仓库](../../../agents/issue-tracker.md)。

## 任务总表

### 基础任务

| ID | 任务 | status | executor | blocked by |
|---|---|---|---|---|
| F-01 | [九模块骨架与 integration 提取](f-01-nine-module-skeleton-and-integration.md) | `done` | `agent` | H-01 |
| F-02 | [identity 更名、可信上下文与公开端口](f-02-identity-boundary-and-trusted-context.md) | `done` | `agent` | F-01 |
| F-03 | [funds/device/recycling/operations 边界搬迁](f-03-business-module-boundary-migration.md) | `ready` | `agent` | F-02 |
| F-04 | [目标数据库 V1～V4](f-04-database-v1-v4-iam-device.md) | `done` | `agent` | 无；合入新应用和联合验收前需 F-01 |
| F-05 | [目标数据库 V5 recycling](f-05-database-v5-recycling.md) | `ready` | `agent` | F-04 |
| F-06 | [目标数据库 V6～V10](f-06-database-v6-v10-funds-operations.md) | `blocked` | `agent` | F-05 |
| F-07 | [epoch guard 与空目标库 Fake bootstrap](f-07-epoch-guard-and-fake-bootstrap.md) | `blocked` | `agent` | F-03、F-06 |
| F-08 | [inbox 与可靠任务 tracer](f-08-inbox-reliable-task-tracer.md) | `blocked` | `agent` | F-03、F-06 |
| F-09 | [HTTP OpenAPI 3.1 与客户端传输基础](f-09-http-openapi-client-transport.md) | `ready` | `agent` | F-02 |
| F-10 | [OneNet Schema 与 UART Registry 冻结](f-10-onenet-schema-uart-registry.md) | `blocked` | `mixed` | 通用三语言黄金样本与 `0x300` HIL 已通过；等待 MCU 实际工具链黄金程序 |
| F-11 | [香橙派 SQLite、OneNet/COS 与 UART 基础](f-11-edge-sqlite-onenet-cos-uart.md) | `in-progress` | `agent` | 软件实施已授权；F-10 仍是进入评审/完成门 |
| F-12 | [完整试点 seed 编排](f-12-pilot-seed-orchestration.md) | `blocked` | `mixed` | V-01、V-03、V-07、V-10 |

### 纵向业务任务

| ID | 任务 | status | executor | blocked by |
|---|---|---|---|---|
| V-01 | [租户、机构和工作人员可以安全登录管理](v-01-tenant-organization-staff-login.md) | `blocked` | `agent` | F-02、F-03、F-04、F-09 |
| V-02 | [机构用户首次注册并获得独立零余额钱包](v-02-organization-user-registration-wallet.md) | `blocked` | `mixed` | V-01、F-06、F-09 |
| V-03 | [试点设备从库存到配置可用](v-03-pilot-device-deployment-configuration.md) | `blocked` | `mixed` | V-01、F-07、F-08、F-11、H-03 |
| V-04 | [一次真实投递形成待审核订单](v-04-real-delivery-pending-review.md) | `blocked` | `mixed` | V-02、V-03 |
| V-05 | [投递审核/纠错形成真实钱包差额](v-05-delivery-review-wallet-delta.md) | `blocked` | `agent` | V-04 |
| V-06 | [连续投递、断网与迟到结果恢复](v-06-continuous-delivery-recovery.md) | `blocked` | `mixed` | V-04、V-08 |
| V-07 | [一次真实清运完成换袋](v-07-real-cleaning-bag-swap.md) | `blocked` | `mixed` | V-02、V-03 |
| V-08 | [满溢、基准和精确安全恢复](v-08-fullness-baseline-precise-recovery.md) | `blocked` | `mixed` | V-04、V-07 |
| V-09 | [Native 充值软件闭环](v-09-native-recharge-software-loop.md) | `blocked` | `agent` | V-02、F-08 |
| V-10 | [手动提现软件闭环](v-10-manual-withdrawal-software-loop.md) | `blocked` | `agent` | V-05、V-09、F-08 |
| V-11 | [告警、审计、对账与一致概览](v-11-operations-governance-overview.md) | `blocked` | `agent` | F-08、V-04、V-07、V-08、V-10 |

### HITL 任务

| ID | 任务 | status | executor | blocked by |
|---|---|---|---|---|
| H-01 | [旧栈恢复单元和所有权清单](h-01-legacy-stack-recovery-baseline.md) | `done` | `human` | 无 |
| H-02 | [目标数据库身份与环境供应](h-02-target-database-identities-environment.md) | `blocked` | `human` | F-06 |
| H-03 | [MCU UART 1.0 固件与真机基础验收](h-03-mcu-uart-firmware-acceptance.md) | `blocked` | `human` | F-10；仅完成 F-11 所需 `0x300` 挥发 HIL 切片，完整实施未授权/未验收 |
| H-04 | [真实 Native 充值](h-04-real-native-recharge.md) | `blocked` | `human` | V-09、`EXT-WECHAT-NATIVE-READY` |
| H-05 | [真实商家转账与微信零钱到账](h-05-real-merchant-transfer.md) | `blocked` | `human` | V-10、H-04、`EXT-WECHAT-TRANSFER-READY` |
| H-06 | [成对切换、回退演练与 M0 签署](h-06-paired-cutover-m0-signoff.md) | `blocked` | `human` | H-01、H-02、H-03、H-04、H-05、F-07、F-12、V-05、V-06、V-07、V-08、V-09、V-10、V-11 |

## 依赖导航

主要基础链：

```text
H-01 → F-01 → F-02 → F-03
F-04 → F-05 → F-06
F-03 + F-06 → F-07 / F-08
F-02 → F-09
F-10 → F-11
F-10 → H-03
```

设备和回收链：

```text
V-01 → V-02
V-01 + F-07 + F-08 + F-11 + H-03 → V-03
V-02 + V-03 → V-04 / V-07
V-04 → V-05
V-04 + V-07 → V-08
V-04 + V-08 → V-06
```

资金和治理链：

```text
V-02 + F-08 → V-09 → H-04
V-05 + V-09 + F-08 → V-10
V-10 + H-04 → H-05
F-08 + V-04 + V-07 + V-08 + V-10 → V-11
V-01 + V-03 + V-07 + V-10 → F-12
```

最终门：

```text
H-01 + H-02 + H-03 + H-04 + H-05
+ F-07 + F-12
+ V-05 + V-06 + V-07 + V-08 + V-09 + V-10 + V-11
→ H-06
```

上面的简图用于导航；发生疑义时，以[批准的完整依赖图](../../detailed-design/08-implementation-sequence.md)和各任务 `blocked_by` 为准。

## 外部条件

以下标识不是 EcoBin 任务，没有 owner、effort 或 `done` 状态；它们只记录真实渠道是否已经具备验收条件：

| 外部条件 | 当前状态 | 解除方式 | 阻塞任务 |
|---|---|---|---|
| `EXT-WECHAT-NATIVE-READY` | 未就绪 | 项目负责人和微信商户配置操作者确认真实 Native 支付、回调/查单和受控 acceptance 条件已经可用 | H-04 |
| `EXT-WECHAT-TRANSFER-READY` | 未就绪 | 项目负责人和指定测试用户确认真实商家转账、用户确认页、查单/通知和小额验收条件已经可用 | H-05 |

外部条件解除后，主审仍需重新核对对应软件任务、凭证安全、白名单和小额边界，才能把 H-04/H-05 从 `blocked` 改为 `ready`。

## 领取与更新规则

1. 先读仓库根 `CLAUDE.md`；涉及跨端、IoT、硬件或历史决策时再读
   `docs/architecture/project-context.md`。
2. 领取前检查任务全部 `blocked_by`、当前工作区和冻结设计；发现冲突时停止并交给主审。
3. 只有明确负责人且获得实施授权后，才把任务改为 `in-progress`。
4. 验证证据、阻塞变化和主审结论追加到任务的“进展记录”，不得另建私人任务清单。
5. Mixed 任务的软件完成不能关闭任务；真实联调和人工验收必须分别有证据。
6. 依赖变化必须同步第 08 章、任务文件和本索引，由主审统一修订。

## 里程碑真实性

只有按证据使用以下名称：

```text
FOUNDATION_READY
DEVICE_CONTROLLED_SLICE_COMPLETE
SOFTWARE_SIMULATION_COMPLETE
M0_COMPLETE
```

`M0_COMPLETE` 只在 H-06 的全部真实门槛通过并由项目负责人签署后使用。Fake 微信、部分场景、OneNet/UART ACK 或任务 `DONE` 都不能替代业务闭环。

## 权威来源

- [详细设计总索引](../../detailed-design-draft.md)
- [第 08 章：任务依赖、领取规则与目标窗口](../../detailed-design/08-implementation-sequence.md)
- [工程领域上下文](../../../agents/domain.md)

## 进展记录

- 2026-07-23：29 项任务按批准依赖发布；任务发布不构成实施授权。
- 2026-07-23：主审完成 29 个唯一 ID、frontmatter、状态/执行主体、依赖 DAG、索引、相对链接和粗估复核；修正 Mermaid 与正式 `blocked_by` 的 4 处边不一致，最终 65 条内部依赖边完全一致，索引审查完成。
- 2026-07-24：不改变任务数量、依赖、status/executor 或授权状态，更新 F-03～F-06、F-10/F-11、V-04/V-06～V-08 和 H-03 的验收语义。
- 2026-07-24：项目负责人授权并完成 H-01、F-01；F-02 因前置完成转为 `ready`，但未
  获编码授权。确认 F-10 软件阶段完成并可导入 OneNet，任务级状态改为 `blocked`，等待
  MCU、真实三语言工具链和联调验收收口。当前共 `done` 2、`ready` 2、`blocked` 25。
- 2026-07-24：项目负责人授权并完成 F-02、F-04；F-03、F-05、F-09 的任务依赖随之
  解除并转为 `ready`，但三项均未获得实施授权。依赖图和各任务 `blocked_by` 不变，
  F-10 继续保持 `blocked`。当前共 `done` 4、`ready` 3、`blocked` 22。
- 2026-07-24：MCU 负责人接受 UART Registry 的消息数值、状态机边界、能力位和非易失
  能力；Java/Python/C 同一黄金样本证据仍未齐全，F-10 整体继续保持 `blocked`，H-03
  也未获得固件实施授权。
- 2026-07-24：项目负责人授权 F-11 基于现有机器来源开始软件实施，任务进入
  `in-progress`；F-10 继续作为 F-11 进入评审和完成的门。当前共 `done` 4、
  `ready` 3、`in-progress` 1、`blocked` 21。
- 2026-07-25：真实 Python 3.11 与香橙派 GCC 12.2 C11 黄金样本通过；F-11 完成
  OneNet 命令可靠受理和 `APPLY_CONFIGURATION` UART 配置纵切。真实 MCU 没有回应
  `HELLO`，因此 F-10、F-11 和 H-03 的任务级状态保持不变。
- 2026-07-25：真实香橙派与 `1.0.0-hil.3` MCU 通过 `0x300` 一投口 HELLO、配置、
  重复配置去重和 QUERY_STATE HIL，修复 boot ID 越界与 SHA 栈覆盖。F-10 仍等待 MCU
  实际工具链黄金程序，F-11 仍缺完整能力、持久恢复、其他物理命令与故障注入，H-03
  未完成全量授权/验收；三项任务状态和总状态数均不变。
