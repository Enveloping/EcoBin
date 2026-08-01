# EcoBin P0 目标数据库设计：表族与事实所有权（D-006～D-010）

> 总索引：[database-design-draft.md](../database-design-draft.md)
>
> 状态：**D-006～D-010 已确认**
>
> 说明：本文件是数据库设计草案的分章正文，与其余章节共同组成一份设计；决策编号与原正文保持不变。

## 6. 第二批已确认：目标表清单与事实所有权

本批只确认“为什么需要这些独立事实及由谁写入”，暂不确认列、枚举、索引和 DDL。表的数量不能单独作为合并理由；同时，后续字段设计若证明两个候选表只是同一聚合的一对一附属字段，也可以在不丢失约束、不可变历史和模块所有权的前提下提出合并，再单独确认。

### D-006 目标表按模块使用独立前缀

**已确认：新数据库不再延续含义过宽的 `sys_`/`biz_`，采用 `iam_`、`dev_`、`rec_`、`fund_`、`ops_` 五组前缀，并让前缀直接表示唯一写入模块。**

- `iam_`：组织、身份与授权，由 identity 模块写入。
- `dev_`：物理设备、部署、配置和设备作业，由 device 模块写入。
- `rec_`：投递交易、清运、袋和容量，由 recycling 模块写入。
- `fund_`：钱包、机构额度、充值、提现和微信资金渠道事实，由 funds 模块写入。
- `ops_`：可靠执行、审计、隔离、对账和告警，由 operations 模块写入。
- common、framework、integration 和 bootstrap 不拥有业务表；integration 只把外部协议转换为 inbox 或业务端口输入，Flyway 集中放在 bootstrap 不改变表的业务所有权。
- 第一版 V1 只创建 M0 闭环和其正确性约束实际需要的表；M1/M2 能力以后通过前向迁移增加，不创建没有用例、只有“以后可能用到”的空表。
- `ops_` 记录可能是平台级、租户级、机构级或暂未识别作用域，因此作为 D-002 的技术控制面例外：从 MyBatis 自动租户拦截中排除，只能通过 operations 端口访问。`scope_kind=TENANT` 时必须只有 `tenant_id`，`scope_kind=ORGANIZATION` 时必须同时具有 `tenant_id + organization_id` 并满足复合外键，`PLATFORM/UNRESOLVED` 时两者都为空；任何情况都不得伪造默认租户。
- 2026-07-24 收口并已执行的首版共 **83 张历史表**：identity 14 张、device 16 张、recycling 24 张、funds 20 张、operations 9 张。2026-08-01 取消清运审核并允许清运记录直接修改后，V20 删除 `rec_clean_revision`、新增 `rec_clean_record_change`，目标业务清单仍为 **83 张表**（recycling 24 张）；V20 实施前实际 schema 同样是 83 张，但两套清运结构语义不同。原投递周期候选表已删除；会话内继续开关门只属于香橙派本地流程，不以另一张云端表保存。

### D-007 identity 表族

**已确认：平台账号、租户工作人员和机构小程序用户保持三类身份；账号、任职、能力和会话分别建模。**

| 正式表名 | 唯一负责的事实 |
|---|---|
| `iam_platform_admin` | 一个平台管理员账号；不是任何租户成员 |
| `iam_tenant` | 一个企业租户 |
| `iam_organization` | 租户下一个一级机构 |
| `iam_organization_miniapp` | 一个机构与小程序 AppID 的身份映射、登录启停和非密钥配置；AppID 全局唯一，不拥有微信支付商户绑定验证 |
| `iam_staff_account` | 一个租户内 Web 工作人员账号；账号内建类型只区分租户主体账号与普通工作人员账号 |
| `iam_organization_staff_membership` | 工作人员在机构的任职关系及负责人身份 |
| `iam_permission_definition` | 由代码/迁移维护的稳定能力目录，不是租户可随意创造的角色 |
| `iam_staff_permission_grant` | 普通工作人员在租户或机构作用域得到的一项能力 |
| `iam_staff_miniapp_binding` | 一个工作人员账号与当前机构 AppID/OpenID 身份的可撤销管理端绑定；只负责免密识别，不授予权限 |
| `iam_organization_user` | 一个机构 AppID/OpenID 下的 C 端用户、手机号绑定和启停事实 |
| `iam_organization_user_capability` | 机构用户的一项附加能力；P0 至少支持清运能力，不另建“清运员用户” |
| `iam_platform_login_session` | 平台管理员的一次可强制失效登录会话 |
| `iam_staff_login_session` | 租户工作人员的一次可强制失效登录会话 |
| `iam_organization_user_session` | 机构用户的一次绑定机构小程序的登录会话 |

租户主体账号作为 `iam_staff_account` 的内建账号类型保留，天然拥有租户全部能力；普通账号不再编码“总部员工/机构员工”类型，其任职和可管理范围由机构任职及权限授予表达，因此同一总部员工可以管理多个机构。机构负责人由任职关系表达，天然拥有本机构全部能力，不靠批量伪造授权记录。三类会话虽然共享实现机制，但分别建表以保留真实外键和清晰作用域，不使用 `principal_type + principal_id` 裸多态引用。

作用域分类固定为：`iam_platform_admin`、`iam_tenant`、`iam_permission_definition` 和平台会话属于平台级；工作人员账号及其会话属于租户级；机构、机构小程序、机构任职、机构级授权、机构用户、用户能力及用户会话属于机构级并强制同一 `tenant_id + organization_id`。租户级权限授予只有 `tenant_id`，机构级权限授予还必须带 `organization_id`，由作用域类型和约束区分。

### D-008 device 表族

**已确认：物理资产、机构部署、配置发布、运行投影、作业占用和物理证据分层，不能重新合成一张万能设备表或状态表。**

| 正式表名 | 唯一负责的事实 |
|---|---|
| `dev_device_asset` | 一台平台级物理设备及不可变 SN、型号等库存事实；OneNet 产品来自系统配置，设备名由 SN 推导，不重复落列 |
| `dev_device_deployment` | 该资产在某机构、地点的一次不可变部署历史 |
| `dev_asset_active_deployment` | 物理资产当前唯一有效部署关系 |
| `dev_port` | 一次部署下的一个逻辑投口 |
| `dev_config_version` | 一次不可变的设备配置发布 |
| `dev_port_config_snapshot` | 某配置版本下一个投口的价格、满溢和作业参数快照 |
| `dev_config_application` | 配置被香橙派可靠保存并应用确认的过程 |
| `dev_deployment_runtime_state` | 部署当前在线、MCU、整机门安全和核心健康投影 |
| `dev_port_runtime_state` | 投口门及传感器当前健康投影；不重复保存 recycling 的容量/满溢真相 |
| `dev_device_fault_event`（D-029 已确认补充） | 一个设备/投口故障从首次确认到恢复的连续事件；当前健康投影不能替代其历史身份 |
| `dev_device_occupancy` | 物理设备当前唯一 DELIVERY/CLEAN 执行权槽位 |
| `dev_delivery_session` | 一名机构用户对整机的一次连续投递作业；同时是开始授权快照、唯一最终物理结果和唯一订单的幂等根 |
| `dev_device_command` | 一个稳定领域命令的身份、目标、语义参数和物理状态投影；不拥有任务租约或协议发送重试 |
| `dev_edge_event`（I-041 已确认补充） | 所有可信边缘事件共享的不可变规范头；统一拥有事件 UUID、部署内全局持久序号、Schema、来源 inbox 和内容摘要 |
| `dev_device_command_event` | ACK/NACK、开关门或失败等只追加命令观察 |
| `dev_physical_result` | 已认证、规范化的设备物理结果证据，不等于业务订单或清运记录 |

设备原始外部消息由 `ops_inbox_message` 保存，规范事件身份由 `dev_edge_event` 保存，命令/物理类型细节由对应 device 子表保存，订单认定由 recycling 保存；各层通过稳定作业 ID 和内容摘要关联，不互相覆盖。`dev_device_command_event` 只保存命令级 ACK/物理观察，`dev_physical_result` 只保存作业级完成证据，两者各自以唯一 `edge_event_id` 一对一引用公共事件头，同一个观察不能在两表形成两份可独立解释的结果。

`ops_reliable_task` 是设备命令唯一可领取、租约化、重试的外部发送意图及协议执行快照，并引用 `dev_device_command`；device 只决定领域命令及其物理状态，不能自行建立第二套发送重试状态。下一场需要中心授权的新作业“能否开门”由配置、健康、容量和占用共同判断，不再在多张表各存一份互相冲突的 `available` 真相；已授权投递 session 内的本地继续不重新执行该中心判断。

设备表中只有 `dev_device_asset` 是平台级。部署/current deployment、投口、配置/应用、运行状态、占用、投递会话、命令/公共事件头/类型子表和物理结果在 P0 都必须固化 `tenant_id + organization_id`，并通过部署复合关系校验。无法确认机构作用域的消息只能进入 `ops_message_quarantine`，不得写成无租户的 `dev_edge_event` 或 `dev_physical_result`。

投递会话内可以在香橙派上发生多次“关门后继续投递”，但这些本地轮次没有云端身份、序号、命令事件、物理结果、照片或重量记录。中心只授权一次 session，并只接收以该 `session_uid` 为根的最终 `DELIVERY_COMPLETE`；每个 session 最多一个 `dev_physical_result`，继续动作不得建立第二个结果。

> I-041～I-045 的补充不改变 D-008 事实所有权：`dev_edge_event` 只把此前分散在命令事件和物理结果中的跨类型事件身份、部署内序号及规范摘要提升为单一公共头。它不保存 inbox 原文、不替代强类型子表，也不拥有订单、照片槽或业务确认任务。

> D-029 已确认补充不改变 D-008 的其他表：`dev_device_fault_event` 是 device 表族唯一新增项，用来补足“device 拥有故障发生/恢复、operations 只做告警投影”所必需的持续事件身份。

### D-009 recycling 表族

**已确认：投递、清运、袋关系和满溢分别保存原始事实、当前投影及只追加历史。**

| 子域 | 正式表名及事实 |
|---|---|
| 机构投递规则 | `rec_organization_delivery_config`（机构级不可变配置版本，M0 至少保存全部人工审核模式、负余额停投阈值和人工认定重量绝对值上限）、`rec_organization_delivery_config_head`（当前版本的唯一可锁指针） |
| 机构清运规则 | `rec_organization_clean_config`（机构级不可变配置版本，M0 保存清运总时限，不包含审核模式）、`rec_organization_clean_config_head`（当前版本的唯一可锁指针） |
| 投递 | `rec_organization_order_counter`（机构内订单提交可见序号的唯一分配根）、`rec_delivery_order`（不可变订单来源快照及当前认定投影）、`rec_delivery_anomaly`（用户/系统异常，只追加）、`rec_delivery_revision`（首次审核和每次纠错版本）、`rec_delivery_photo`（四个标准位置的照片关联或缺失事实） |
| 清运 | `rec_organization_clean_record_counter`（机构内清运记录提交可见序号的唯一分配根）、`rec_clean_operation`（开门前建立、可恢复的状态机）、`rec_clean_record`（完成后才产生，保存原始快照与当前有效业务值）、`rec_clean_record_change`（每次直接修改的只追加前后值、原因和操作者）、`rec_clean_anomaly`（只追加系统异常）、`rec_clean_photo`（清运四个标准位置）；不建立清运审核决定或待审状态表 |
| 袋与重量基准 | `rec_bag`（机构内可复用袋码身份，无生命周期状态）、`rec_bag_current_occupancy`（投口绑定或清运预留二选一）、`rec_bag_occupancy_event`（只追加关系历史）、`rec_port_weight_baseline`（每次有效皮重版本）、`rec_port_baseline_measurement`（当前空袋真实重测过程）、`rec_port_capacity_state`（当前基准、最近重量和容量投影） |
| 满溢 | `rec_fullness_detection`（一次完整检测流程和配置快照）、`rec_fullness_sample`（初检/复检不可变采样或失败）、`rec_fullness_event`（一次从确认满溢到恢复的持续事件） |

- 不另建 `rec_delivery_raw_result`：设备层保存物理证据，订单保存被业务接受后的原始重量、价格、袋码和原始金额快照，避免两套订单真相。
- `rec_organization_delivery_config` 每次修改创建新版本，并通过 head 原子切换当前版本；不把投递审核规则、负余额停投阈值或人工认定上限塞进 `iam_organization`。投递开始授权先锁 head，再锁定配置版本及本 session 实际需要的关键值，订单继续保留对应快照；之后修改机构规则不追溯影响已经开始的 session。会话内继续投递不再次访问中心配置或资金闸门。
- `rec_organization_clean_config` 使用相同的 head 发布模式，避免清运准备通过 `MAX(version_no)` 与并发发布形成两个“当前版本”。M0 初始化时必须为试点机构共同建立首个配置和 head；以后配置接口发布新版本时原子切换 head。
- 清运操作与清运记录不能合并；开门后未完成仍是待恢复操作，不是成功清运事实。
- 袋码在 EcoBin 全平台唯一，`rec_bag` 同时固化其 `tenant_id + organization_id` 归属；P0 只允许在所属机构使用。实体袋可以反复使用，但不能同时被两个投口绑定或被另一个清运操作预留。若以后允许跨机构流转，再把袋提升为平台资产，不在 P0 预建该能力。
- 清运员属于可信工作人员，其完成确认直接形成清运记录。清运不建立审核状态或审核决定；`clean.edit` 通过记录版本条件直接修改当前有效业务值，并在同一事务追加 `rec_clean_record_change`。设备原始结果与系统异常保持只追加；重量统计读取记录的当前有效重量，清空后即从已知重量统计排除。
- 清运完成先把容量状态置为“待清运后检测”；后续真实满溢检测结果独立收敛状态。它不因换袋本身伪造“不满”。
- I-026～I-030 的稳定查询要求每笔已提交清运记录取得机构内唯一可见序号；空袋基准恢复要求独立保存重测身份、冻结袋/配置代际和真实设备结果。二者分别由清运记录计数器和基准重测表承担，不能用列表当前最大值或设备命令行临时推导。
- 投递照片与清运照片使用强类型表，不使用无法建立可靠外键的通用 `biz_type + biz_id` 照片表；迟到照片补入原业务位置，缺失不进入用户异常，也不阻断订单、返现或清运完成。

### D-010 funds 与 operations 表族

**已确认：用户钱包和机构出款账户保持两套账本；业务单、微信渠道单、渠道观察和可靠执行任务分别建模。**

| 子域 | 正式表名及事实 |
|---|---|
| 机构提现配置 | `fund_organization_withdraw_config`：M0 保存本机构手动提现范围、免审阈值和单次硬上限；`fund_organization_withdraw_config_head`：原子指向当前版本。业务单继续固化创建时快照；自动提现开关、范围和免审字段在 M1 以前向迁移增加 |
| 用户钱包 | `fund_organization_wallet_entry_counter`（机构内用户钱包明细提交可见序号的唯一分配根）、`fund_user_wallet`（当前可用/冻结投影）、`fund_user_wallet_entry`（不可变资金明细）、`fund_wallet_adjustment`（人工调整命令及操作者/原因事实） |
| 机构额度 | `fund_organization_payout_account`（当前可用/冻结投影）、`fund_organization_payout_entry`（不可变资金明细） |
| 充值 | `fund_recharge_order`（金额、费率、手续费、净额和业务状态）、`fund_wechat_payment`（一对一支付渠道单及当前投影）、`fund_wechat_payment_observation`（回调/查单/创建响应/对账的不可变观察） |
| 提现 | `fund_withdrawal_order`（申请、双侧冻结、收款身份/配置快照和业务状态）、`fund_withdrawal_review`（不可变审核决定）、`fund_active_withdrawal`（每钱包唯一进行中占位）、`fund_wechat_transfer`（一对一固定 `out_bill_no`、请求快照和渠道投影）、`fund_wechat_transfer_observation`（不可变渠道证据） |
| 系统商户与闸门 | `fund_wechat_merchant_profile`（普通商户的非敏感身份配置，密钥仍外部注入）、`fund_miniapp_merchant_binding`（机构 AppID 与系统商户号已经完成渠道绑定验证的唯一事实）、`fund_payout_gate`（每个系统商户资金池的当前出款闸门）、`fund_payout_gate_event`（暂停/恢复的不可变事实） |
| 可靠执行 | `ops_inbox_message`（已验证外部收件）、`ops_reliable_task`（外部 outbox 意图、延时动作和 inbox 处理任务的唯一可执行记录）、`ops_task_attempt`（每次领取/调用/结果的不可变尝试） |
| 运营证据 | `ops_audit_log`、`ops_message_quarantine`、`ops_reconciliation_run`、`ops_reconciliation_issue`、`ops_reconciliation_action`、`ops_alert` |

不再同时建立 `ops_outbox`、`ops_job` 和另一套任务表。`ops_reliable_task` 是唯一执行真相：业务事务直接写入外部动作意图或内部定时任务，执行器在同一行上领取租约并把每次尝试追加到 `ops_task_attempt`。可信外部消息写入 `ops_inbox_message` 时，必须在同一事务创建唯一的 `PROCESS_INBOX` 任务，并以唯一约束保证一个 inbox 只有一个处理任务；只有该事务提交后才向 OneNet/微信 ACK，避免出现“已经收件但永远没有处理任务”的断链。

I-033 的提现配置采用与投递/清运配置相同的 head 发布语义：发布事务锁定 head、校验 `expected_current_version`、插入新不可变版本并切换指针；提现创建先锁当前 head 再固化完整配置快照。不能继续通过 `MAX(version_no)` 猜当前，也不能让配置发布和提现创建各自观察到不同的“当前版本”。

三类证据的边界固定为：`ops_inbox_message` 解释可信入站传输、外部消息幂等和内容摘要；`ops_task_attempt` 解释一次技术领取/调用及 HTTP、超时、错误分类；`fund_wechat_*_observation` 解释解析和验证后的微信业务状态。后两者必须引用来源 inbox 或 task attempt，不能各自复制一份可独立解释的渠道终态。类似地，`fund_wallet_adjustment` 是余额调整业务事实，`ops_audit_log` 只解释操作主体和请求上下文，不能成为第二份资金调整真相。任务只解释“需要执行什么以及是否重试”，订单和微信渠道终态仍由 funds/device/recycling 各自解释。

机构小程序“可以登录”和“可以使用真实微信转账”是两个事实：identity 的 `iam_organization_miniapp` 只负责 AppID 到机构的可信身份映射；funds 的 `fund_miniapp_merchant_binding` 唯一负责该 AppID 是否已经与指定系统商户号完成渠道绑定验证、验证时间及当前提现可用性。提现创建必须同时引用两者，不能仅凭小程序已启用就认定渠道已经就绪。

领域状态与运营告警同样不能互相替代：`rec_fullness_event`、`fund_payout_gate_event` 和 device 的运行健康事实分别拥有满溢、出款暂停/恢复及设备故障的发生与恢复；`ops_alert` 只是以 `source_type + source_id` 唯一引用这些领域事实的展示和人工确认投影，可以更新发现次数、最近发现时间和已查看状态，但不能自行把领域故障标记为恢复。只有领域模块提交恢复事实后，operations 才关闭相应告警投影；M1 真正实现外部通知时再以前向迁移增加通知投递事实，不在 M0 预建常量字段。

I-040 的运营概览不是新的业务表族：operations 通过 identity、device、recycling、funds 和自身公开查询端口读取各自主权事实，在专用只读 `REPEATABLE READ` 事务中组装同一快照。M0 不建立跨模块统计累计表、缓存余额或可人工修正的指标投影；所有业务写事务仍使用 D-036 已冻结的 `READ COMMITTED`。

#### 异常与失败的唯一落点

| 发生层次 | 唯一事实表 | 不应写入 |
|---|---|---|
| 签名或来源身份不可信 | 脱敏安全日志及 `ops_alert` 聚合；不形成可信业务记录 | `ops_inbox_message`、`dev_physical_result`、`rec_*_anomaly` |
| 已认证但永久格式错误、同 ID 内容冲突或无法确定作用域 | `ops_message_quarantine` | 业务订单、设备物理结果 |
| 任务领取、网络、HTTP、超时或适配器调用失败 | `ops_task_attempt`；原业务状态保持未完成/未知并按任务策略恢复 | 用户/清运异常表 |
| 命令 ACK/NACK、门动作或命令级物理失败 | `dev_device_command_event`，必要时更新 device 运行健康投影 | `ops_message_quarantine`、订单异常 |
| 已认证作业完成结果、首次/最终称重、最终 `negativeWeightAnomaly` 标志或设备故障码 | `dev_physical_result` | 直接认定用户违规或资金结果 |
| 投递最终负重量异常标志、已接受 session 中的重量/价格/归属等业务异常 | `rec_delivery_anomaly`；保留对应 `dev_physical_result` 作为来源证据 | `ops_task_attempt` 或任何单独“负重量事件” |
| 清运完成事实中的皮重、净重或安装数据异常 | `rec_clean_anomaly`；不回滚已经发生的物理换袋，也不生成审核任务 | 设备命令事件、人工审核或通用告警替代清运异常事实 |
| 业务数据库事务失败 | 不产生半条业务异常；事务回滚，`PROCESS_INBOX` 任务保留重试并记录 `ops_task_attempt` | `rec_*_anomaly` |
| 微信调用技术结果与已验证渠道状态 | 技术执行写 `ops_task_attempt`；业务状态写对应 `fund_wechat_*_observation` 并引用来源 | 两边各保存一份可独立驱动结算的终态 |
| 对账发现跨表/跨渠道矛盾 | `ops_reconciliation_issue`，不确定时停止自动资金修改 | 伪造微信终态或直接覆盖账本 |

`NOT_ENOUGH` 只追加微信观察、保持原提现双侧冻结并关闭平台闸门；不能把微信转账单改为失败。只有 `SUCCESS`、`FAIL`、`CANCELLED` 驱动资金终态。
