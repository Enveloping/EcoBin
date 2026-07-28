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
> F-03、F-04、F-05、F-06、F-07、F-08、F-09、F-10；
> F-11 已获软件实施授权并处于 `in-progress`；V-01 已完成；H-02 已通过本地 MySQL 8.4
> 开发演练及服务器整改阶段 0～3 验收，项目负责人接受当前试验期使用 ACL 受限
> `.ecobin` 保管长期凭证原件，任务已转为 `done`；
> V-02 为 `ready` 但仍须单独授权；其他任务仍须逐项获得授权。
> `status: ready` 只表示任务设计和前置依赖允许领取，不构成后续任务的自动授权。

## Initiative 状态

| 项目 | 当前值 |
|---|---|
| Initiative | `p0-controlled-loop` |
| 任务数 | 29（F-01～F-12、V-01～V-11、H-01～H-06） |
| 设计状态 | 详细设计、任务粒度、依赖和执行分类已批准；2026-07-24 已同步投递 session/清运电子锁修订 |
| 实施授权 | **部分授权：H-01、H-02、F-01、F-02、F-03、F-04、F-05、F-06、F-07、F-08、F-09、F-10、V-01 已授权并完成；F-11 固定帧适配实施中；H-02 当前试验期 `.ecobin` 凭证保管例外已接受；V-02 已 ready 但未授权；H-03 真机验收及阶段 4 其他任务未授权** |
| 当前状态数 | `done` 13、`ready` 1、`in-progress` 1、`blocked` 14 |
| 风险目标 | 2026-07-30 只用于风险排序，不构成 G1、G2 或 M0 承诺 |
| 权威依赖来源 | [第 08 章](../../detailed-design/08-implementation-sequence.md) |

## 初始工作量审查

| executor | 任务数 | 工作量 |
|---|---:|---:|
| `agent` | 15 | 67～112 person-days |
| `mixed` | 8 | 49～82 person-days |
| `human` | 6 | 7～15 person-days |
| **合计** | **29** | **123～209 person-days** |

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
| F-03 | [funds/device/recycling/operations 边界搬迁](f-03-business-module-boundary-migration.md) | `done` | `agent` | F-02 |
| F-04 | [目标数据库 V1～V4](f-04-database-v1-v4-iam-device.md) | `done` | `agent` | 无；合入新应用和联合验收前需 F-01 |
| F-05 | [目标数据库 V5 recycling](f-05-database-v5-recycling.md) | `done` | `agent` | F-04 |
| F-06 | [目标数据库 V6～V10](f-06-database-v6-v10-funds-operations.md) | `done` | `agent` | F-05 |
| F-07 | [epoch guard 与空目标库 Fake bootstrap](f-07-epoch-guard-and-fake-bootstrap.md) | `done` | `agent` | 两项 P1 补强后通过复审并合入 |
| F-08 | [inbox 与可靠任务 tracer](f-08-inbox-reliable-task-tracer.md) | `done` | `agent` | F-03、F-06 |
| F-09 | [HTTP OpenAPI 3.1 与客户端传输基础](f-09-http-openapi-client-transport.md) | `done` | `agent` | F-02 |
| F-10 | [OneNet Schema 与 UART Registry 冻结](f-10-onenet-schema-uart-registry.md) | `done` | `mixed` | 机器来源、通用三语言证据和适配责任边界已确认 |
| F-11 | [香橙派 SQLite、OneNet/COS 与 UART 基础](f-11-edge-sqlite-onenet-cos-uart.md) | `in-progress` | `agent` | 固定帧与 COS 已合入；MQTT 重连、自动诊断和能力降级文档正在收口 |
| F-12 | [完整试点 seed 编排](f-12-pilot-seed-orchestration.md) | `blocked` | `mixed` | V-01、V-03、V-07、V-10 |

### 纵向业务任务

| ID | 任务 | status | executor | blocked by |
|---|---|---|---|---|
| V-01 | [租户、机构和工作人员可以安全登录管理](v-01-tenant-organization-staff-login.md) | `done` | `agent` | F-02、F-03、F-04、F-09 |
| V-02 | [机构用户首次注册并获得独立零余额钱包](v-02-organization-user-registration-wallet.md) | `ready` | `mixed` | V-01、F-06、F-09 |
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
| H-02 | [目标数据库身份与环境供应](h-02-target-database-identities-environment.md) | `done` | `human` | F-06 |
| H-03 | [固定帧 MCU 线路与真机基础验收](h-03-fixed-frame-mcu-hil-acceptance.md) | `blocked` | `human` | F-11；尚未授权真机验收 |
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
F-11 → H-03
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
- 2026-07-25：项目负责人授权 Codex 自行选取并推进后端任务；F-05 在独立 worktree
  完成 V5 的 24 表迁移、逐表验证矩阵和 MySQL 8.4 双空库自动验证，进入 `in-review`。
  F-06 仍须等待 F-05 主审确认并变为 `done`，不提前解除依赖。
- 2026-07-25：项目负责人完成 F-05 审核并确认合并；F-05 转为 `done`，F-06 前置依赖
  解除并转为 `ready`。当前共 `done` 5、`ready` 3、`in-progress` 1、`blocked` 20。
- 2026-07-25：项目负责人授权并完成 F-03；旧 business 退出，reactor 收口为最终九个
  目标模块，跨业务模块仅通过 `.api` 端口协作，JDK 21 下 82 项回归测试和制品安装
  通过。当前共 `done` 6、`ready` 2、`in-progress` 1、`blocked` 20。
- 2026-07-25：项目负责人授权 F-06；在独立 worktree 完成 V6～V10、83 表连续矩阵、
  H-02 最小权限交接和 MySQL 8.4 双空库验收。F-06 转为 `done`，H-02 前置依赖
  解除并转为 `ready`，但真实环境操作仍未授权。当前共 `done` 7、`ready` 2、
  `in-progress` 1、`blocked` 19。
- 2026-07-25：项目负责人完成 F-06 审核并确认合并；审核补强项、V6～V10、验证矩阵
  和阶段入口文档已合入 `database-refactor`，合并后 MySQL 8.4 双空库验证及 Java 21
  全量 82 项测试通过。任务状态数不变。
- 2026-07-26：F-03、F-06 均已完成，F-08 的任务依赖已解除；项目负责人明确授权
  Codex 接取并实施 F-08，任务转为 `in-progress`。当前共 `done` 7、`ready` 2、
  `in-progress` 2、`blocked` 18。
- 2026-07-26：F-08 完成 Fake 可信收件与可靠任务 tracer、稳定完成/唤醒端口、三类
  有界通道配置和 MySQL 8.4.10 真实并发/故障验收，转为 `in-review`。当前共
  `done` 7、`ready` 2、`in-progress` 1、`in-review` 1、`blocked` 18。
- 2026-07-26：F-08 根据 review 补强精确 JSON 数字语义、逐任务即时领取和通道共享
  最大在途限制；Java 21 全仓 91 项回归及 MySQL 8.4.10 专项 5 项通过，状态保持
  `in-review`。
- 2026-07-26：复核 F-07 的 F-03、F-06 前置均已完成；项目负责人明确授权 Codex
  实施 F-07，任务进入 `in-progress`。当前共 `done` 7、`ready` 2、
  `in-progress` 2、`blocked` 18。
- 2026-07-26：F-07 完成 epoch/readiness guard、Fake 外联硬阻断和 MySQL 8.4.10
  真实启动矩阵，转为 `in-review`。当前共 `done` 7、`ready` 2、
  `in-progress` 1、`in-review` 1、`blocked` 18；F-07 仍须项目负责人确认后
  才能转为 `done`，其下游依赖暂不解除。
- 2026-07-26：F-07 根据评审移除生产 JAR 中的 Flyway 运行库/全部迁移脚本，并修复
  servlet context path 对 Fake 入站闩锁的绕过；F-07 独立分支 Java 21 全量 103 项
  测试通过，合入含 F-08 的 `database-refactor` 后 `mvn clean test` 共 112 项、
  0 failure、0 error（5 项 F-08 真实 MySQL 验收按设计跳过），MySQL 8.4.10
  生产 JAR 矩阵再次通过。项目负责人确认更新文档、提交并合入，F-07 转为 `done`。
  当前共 `done` 8、`ready` 2、`in-progress` 1、`in-review` 1、`blocked` 17。
- 2026-07-26：项目负责人授权 Codex 接取 F-09，任务由 `ready` 转为 `in-progress`，
  并建立独立分支和 worktree。按合并后的任务基线，当前共 `done` 8、`ready` 1、
  `in-progress` 2、`in-review` 1、`blocked` 17。
- 2026-07-26：F-09 完成机器契约、Web/小程序传输基础与自动验证，转为 `in-review`。
  按合并后的任务基线，当前共 `done` 8、`ready` 1、`in-progress` 1、`in-review` 2、
  `blocked` 17。
- 2026-07-26：项目负责人确认 F-09 完成，任务转为 `done`。当前共 `done` 9、
  `ready` 1、`in-progress` 1、`in-review` 1、`blocked` 17。
- 2026-07-26：F-08 的两个 P1 复审项已修复并通过 Java 21 全仓 91 项及 MySQL
  8.4.10 专项 5 项验证；项目负责人确认完成，实施提交 `ed5341b` 已合入
  `database-refactor`。F-08 转为 `done`，当前共 `done` 10、`ready` 1、
  `in-progress` 1、`blocked` 17。
- 2026-07-26：复核 V-01 的 F-02、F-03、F-04、F-09 前置均已完成；项目负责人授权
  Codex 在独立 worktree 接取并编码实施 V-01，任务转为 `in-progress`。
- 2026-07-26：V-01 完成目标 Web 会话、identity 目录/审计、管理页面、OpenAPI 和真实
  MySQL 验收，转为 `in-review`；V-02/V-03 等下游仍待项目负责人确认 V-01 完成后
  再解除依赖。
- 2026-07-27：V-01 完成六项 P1 复审修复，真实 MySQL 八项专项、全仓、HTTP 契约和
  Web 回归全部通过；项目负责人要求提交并合入，V-01 转为 `done`。V-02 的软件前置
  全部完成，转为 `ready`，但未获得实施授权。
- 2026-07-26：项目负责人 `enveloping` 明确授权 H-02 并担任人工操作人，选择独立
  MySQL 8.4 容器与独立数据卷供应目标环境；H-02 转为 `in-progress`。
- 2026-07-26：H-02 完成独立 MySQL 8.4.10 容器/数据卷、V1～V10、五类身份、
  完整列级 grants 和脱敏正负测；后续确认该环境只属于本地开发演练，服务器生产供应
  仍未实施。项目负责人决定试验期继续使用当前单机并允许停掉旧后端，H-02 保持
  `in-progress`。
- 2026-07-27：项目负责人授权 H-02 阶段 3；服务器独立目标 MySQL 8.4.10、V1～V10、
  五类身份、最小 grants、正负权限探针、零业务数据和公网 3306/13306 隔离均通过，
  CMS 加密备份完成异机复制、解密读取及一次性独立容器恢复验证，临时恢复资源已删除，
  旧栈和旧卷保留。阶段 3 技术门完成，密码库导入和异机密文副本开启验证仍待操作人
  确认，H-02 保持 `in-progress`。
- 2026-07-27：项目负责人重新裁定当前前期受控试验的凭证保管门：操作机 ACL 受限
  `.ecobin` 明文目录作为当前长期原件位置，明确接受操作机失陷、损坏或丢失导致的
  保密性与可恢复性风险；加密密码库和异机密码库密文副本延期到下一版本加固，不再
  阻断 H-02。主审复核服务器目标数据库、身份、权限、公网隔离、加密备份、隔离恢复
  和仓库秘密扫描均无 P0/P1 问题；三项非阻断改进按负责人决定不在本轮处理。H-02
  转为 `done`。合并 V-01 与 H-02 两条并行工作线后，当前共 `done` 12、`ready` 1、
  `in-progress` 1、`blocked` 15；
  阶段 4、seed、应用部署和成对切换仍未授权。
- 2026-07-27：项目负责人确认三端业务协议已经确定，现有单片机不再以原生实现
  UART 1.0、运行目标工具链黄金程序或通过该协议 HIL 作为 F-10 完成条件；线路差异由
  F-11 的显式固定帧适配层承担，真实设备验收保留在 H-03。F-10 转为 `done`，H-03
  保持 `blocked` 但依赖由 F-10 改为 F-11。当前共 `done` 13、`ready` 1、
  `in-progress` 1、`blocked` 14。
- 2026-07-28：F-11 固定帧适配与照片/COS 链路已合入。项目负责人确认保留完整
  香橙派—后端协议，但 MCU 不支持的配置、远程控制和状态分别采用本地保存、明确失败、
  未知占位；不增加物理命令重发、MCU 作业重启恢复、SQLite/MCU 冲突锁或部署身份启动
  锁。补齐强杀恢复、MQTT 重连和 COS 上传自动测试，Python 3.11 硬件套件为
  `165 passed, 5 subtests passed`，契约套件为 `43 passed, 64 subtests passed`。
  任务保持 `in-progress`，等待本轮代码和文档评审收口；H-03 真机验收边界不变。
