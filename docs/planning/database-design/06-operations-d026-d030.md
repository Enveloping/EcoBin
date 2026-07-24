# EcoBin P0 目标数据库设计：可靠执行与运营治理（D-026～D-030）

> 总索引：[database-design-draft.md](../database-design-draft.md)
>
> 状态：**D-026～D-030 已确认**
>
> 说明：本文件是数据库设计草案的分章正文，与其余章节共同组成一份设计；决策编号与原正文保持不变。

## 10. 第六批已确认：可信收件、可靠执行、审计、告警与对账

本批把 D-010 已确认的 operations 表清单展开为可恢复执行和运营处置字段。四层证据继续严格分开：业务表解释实际业务结果，`ops_audit_log` 解释操作者与请求，`ops_task_attempt` 解释技术执行，`ops_alert` 只表达当前需要关注的问题。任何 operations 记录都不能覆盖设备、订单、账本或微信渠道事实。

### D-026 可信 inbox 与永久隔离

**已确认：只有来源已经验证且具有稳定身份、可确定可信作用域的消息才能进入 inbox；第一份可信内容作为原始证据保留且不可覆盖，永久无效或身份冲突消息进入独立隔离记录，二者都不能被重复投递改写。**

| 表 | M0 核心字段与约束 |
|---|---|
| `ops_inbox_message` | `id`、全局唯一 `inbox_uid`、作用域、`source_namespace + source_principal_key + external_message_id`、消息种类和规范化 schema 版本、有大小上限的原始传输正文及其摘要、可重放的白名单规范载荷及语义摘要、认证方式及非敏感证书/身份引用、关联/因果 ID、`RECEIVED/PROCESSED`、首次/最近接收时间、投递次数和处理时间；唯一 `(source_namespace, source_principal_key, external_message_id)`。 |
| `ops_message_quarantine` | `id`、全局唯一隔离 UUID、由来源/可用身份/内容摘要/原因形成的唯一去重键、作用域、来源与可空外部消息 ID、可空冲突 inbox、`MISSING_STABLE_ID/PERMANENT_FORMAT_ERROR/UNSUPPORTED_SCHEMA/UNRESOLVED_SCOPE/IDENTITY_CONTENT_CONFLICT/SCOPE_CONFLICT`、原始/语义摘要、严格脱敏且限长的诊断载荷、`OPEN/ACKNOWLEDGED`、首次/最近发现时间、发现次数、可空确认审计及锁版本；隔离证据不能形成业务结果。 |

- OneNet 以已认证的产品/设备入口和设备持久生成的 `eventUid` 形成来源身份，`external_message_id=eventUid`；OneNet/Pulsar/MQTT 消息 ID 只放传输元数据，不能回退成为 inbox 或业务幂等身份。微信以通知种类、系统商户号和微信通知 ID 形成来源身份。来源认证、验签/解密、最小格式检查、稳定身份提取和服务端作用域解析都必须先完成；可信 inbox 不允许 `scope_kind=UNRESOLVED`，无法定位原部署、充值单或转账单的已认证消息进入隔离。连可信 `eventUid` 都无法解析的 OneNet 消息只能按传输 ID/摘要进入隔离，不得写 inbox。
- `normalized_content_sha256` 覆盖“消息种类 + 稳定规范化契约版本 + 白名单业务载荷”，忽略 JSON 顺序、空白、传输层重投时间、签名 nonce 等噪声，但必须保留设备/渠道业务发生时间。相同来源协议版本的规范算法以后不得随代码发布漂移；`raw_transport_sha256` 只作原始字节证据。普通查询、日志和导出不得返回原文；正文不保存 HTTP Authorization、密钥、完整 Header 或带凭证 URL，所需读取只开放给受限处理器和技术核查用例。
- 同一外部身份且语义摘要、作用域均相同，只返回原 inbox 并更新最近到达时间和次数；原始正文、规范载荷和首次接收时间都不覆盖。同一身份但语义摘要或作用域不同，新增/刷新隔离记录并引用原 inbox，严禁使用会覆盖正文的盲目 upsert。
- 首次写入可信 inbox 时，必须在同一事务创建唯一 `PROCESS_INBOX:<inbox_uid>` 可靠任务；只有事务提交后才 ACK OneNet 或向微信返回通知成功。相同消息重投只有在原 inbox 和任务已经共同提交后才能 ACK。
- 处理器成功调用业务用例时，业务事实、可能派生的可靠任务、inbox 的 `PROCESSED` 标记和当前处理尝试结果在同一事务提交；失败则全部业务变化回滚，inbox 保持 `RECEIVED`，原任务按策略重试。设备“业务已接收”仍是事务后独立可靠下行，不等于北向 MQ ACK。
- 已认证但确定无法处理的消息在隔离事务提交后 ACK，防止毒消息堵塞；数据库、证书或秘密服务等暂时故障不落“永久隔离”且不 ACK。签名或来源身份不可信的请求既不进入 inbox，也不进入可信隔离，只按渠道返回失败并形成脱敏结构化安全记录和技术条件聚合告警。
- `ACKNOWLEDGED` 仅表示人员已经看过隔离项，不会创建订单、补挂用户、猜测机构或自动重放。M0 不开放隔离消息直接重放；以后确需恢复时必须增加受审计用例，重新建立可验证的稳定处理身份。
- **I-036 接口落实补充**：隔离列表和详情仅向平台固定技术用例开放；确认接口以 `expectedVersion` 条件更新 `OPEN -> ACKNOWLEDGED` 并在同一事务写审计。重复或并发确认最多一次成功，不能创建 inbox、任务或领域事实。查询只返回限长脱敏诊断，原始正文仍不通过管理接口暴露。

### D-027 唯一可靠任务、技术尝试与租约接管

**已确认：`ops_reliable_task` 是唯一可执行记录；每个稳定动作永久只有一个任务，任务租约只协调执行权，真正的幂等和终态仍由稳定业务 ID、领域约束及渠道观察保证。**

| 表 | M0 核心字段与约束 |
|---|---|
| `ops_reliable_task` | `id`、全局唯一 `task_uid`、不可变作用域、`BUSINESS_INTENT/INBOX_PROCESSING/TIMER/RECONCILIATION`、稳定任务类型、`DEVICE/FUNDS` 执行通道、全局唯一 ASCII `task_key`、目标类型/稳定业务键、可空且唯一来源 inbox、可空且唯一设备命令、载荷 schema/脱敏执行快照/摘要、关联/因果 ID和可空发起审计、优先级/重试策略版本/自动尝试上限、`PENDING/DONE/CANCELLED/BLOCKED`、下次执行时间、租约 token/worker/截止时间、尝试序号/连续失败数、单调 `wake_version/handled_wake_version`、完成或阻断信息、时间列和锁版本。 |
| `ops_task_attempt` | `id`、全局唯一 `attempt_uid`、任务、单调尝试号、全局唯一租约 token、领取时 `claimed_wake_version`、worker、领取/租约截止/外部调用可能开始/租约被接管/结果记录时间、本次 `PROCESS/SUBMIT/QUERY/CLOSE/CANCEL`、技术结果分类、请求/响应摘要、HTTP 状态、外部 API 错误码、耗时和限长脱敏诊断；唯一 `(task_id, attempt_no)`，身份与领取字段不可改，调用、接管和结果字段各自最多从空补写一次。 |

- `task_key` 由任务类型、作用域哨兵、稳定业务身份和动作版本规范生成，例如 `PROCESS_INBOX:<inbox_uid>`、`ENSURE_DEVICE_COMMAND:<command_uid>`、`CONFIRM_EDGE_EVENT:<event_uid>`、`PROVIDE_PHOTO_UPLOAD_GRANT:<grant_request_event_uid>`、`POST_RECHARGE:<recharge_order_no>`、`CONVERGE_WECHAT_TRANSFER:<out_bill_no>`、`DAILY_RECONCILIATION:<merchant>:<business_date>`。会话内继续投递是边缘本地动作，不创建任何按轮次执行的中心任务。命中同键时必须比较作用域、目标和载荷摘要；完全一致返回原任务，不一致则生产者事务失败并产生不变量告警，不能覆盖或换键规避。
- `PROCESS_INBOX` 必须且只能引用一个 inbox，任务复制其不可变作用域；其他任务不得占用该字段。任何可执行任务都不允许 `UNRESOLVED`。任务的作用域、类型、通道、目标、稳定键和执行快照创建后不可修改；OneNet/APIv3/COS 密钥、签名、nonce 和临时凭证不进入任务，执行时从外部秘密设施加载或即时生成。
- `PENDING` 同时表示待执行、退避等待或已被短期租用；租约三字段必须同时为空或同时非空。以数据库 UTC 时间和 `claimable_at=COALESCE(lease_until,next_run_at)` 的生成列建立 `(state, execution_lane, claimable_at, priority, id)` 领取索引，领取事务只锁任务、写新租约、递增序号并插入尝试，随后立即提交；外部调用永远在事务外。
- 数据库 `CHECK` 必须保证：`PENDING` 的 `next_run_at` 非空；`DONE/CANCELLED/BLOCKED` 的 `next_run_at` 及租约三字段均为空；租约 token/worker/截止时间只能全空或全非空；`0 <= handled_wake_version <= wake_version`。领取使用数据库时间和 `FOR UPDATE SKIP LOCKED`；不能让空调度时间或半组租约把任务永久漏在扫描之外。
- 实际网络调用前先一次性写 `external_call_may_have_started_at`。一旦该字段成立，就采用“可能已调用”的保守语义；超时、进程崩溃或租约到期均不能宣告渠道失败、释放资金、换 `out_bill_no` 或生成第二个设备命令 ID，下一次只能用原稳定身份查单或幂等续办。
- 租约到期时接管者在任务行锁下给原尝试一次性写 `reclaimed_at`、替换当前租约并建立新尝试，不给旧尝试伪造失败结果。旧执行器以后真实返回时，仍可给自己的尝试一次性补齐技术结果并把可信外部内容送入原幂等业务归并器；失去 token 后不能直接覆盖新执行器的任务决定。
- 为保证迟到证据不会落在已经静止的任务之外，每次领取把当前 `wake_version` 固化到尝试。只有“尝试 token 已不再是当前租约”的迟到结果，或回调/inbox/其他并发路径产生且会改变目标判断的可信证据，才在完成幂等归并后于同一事务锁任务并把 `wake_version + 1`：没有更新一代租约时，即使原状态是 `DONE/CANCELLED/BLOCKED` 也改回 `PENDING` 并立即复检；已有更新一代租约时保留其执行权，但版本变化会阻止它基于旧快照终结任务。当前 token 执行器正常获得的本次响应不自增 wake，而是在同一结果事务直接依据归并后状态安排完成或退避，避免非终态查单形成立即重领热循环。这个唤醒只要求重新核对，不会撤销领域取消、伪造渠道状态或直接恢复业务。
- 当前执行器只有同时满足“租约 token 仍匹配且 `wake_version = claimed_wake_version`”时，才允许把任务推进为 `DONE/CANCELLED/BLOCKED` 或按当前证据安排下一次执行，并把 `handled_wake_version` 推进到该版本；若版本已经增加，只能记录本次真实尝试、释放租约并保持/恢复 `PENDING`。下一次处理重新读取权威领域状态：已收敛则 `NO_ACTION_REQUIRED -> DONE`，仍非终态则继续原 ID 查证，存在矛盾则建立对应业务/对账问题。由此，静止状态只能被可信证据唤醒，普通扫描不能任意重开任务。
- 尝试的结果只允许 `TECHNICAL_SUCCESS/NO_ACTION_REQUIRED/RETRYABLE_FAILURE/OUTCOME_UNKNOWN/PERMANENT_TECHNICAL_FAILURE`，不能保存一份可独立结算的微信 `SUCCESS/FAIL/CANCELLED` 或设备业务终态。微信业务状态只进入 `fund_wechat_*_observation` 并引用尝试，设备命令/物理结果仍进入 device 事实。
- `DONE` 只表示该技术意图不再需要执行，不代表设备动作、充值、提现或微信转账业务成功；有效渠道非终态可以让同一收敛任务继续 `PENDING`。达到自动重试上限进入 `BLOCKED` 并告警，受审计人工恢复仍复用原任务和稳定身份，不能把领域对象改为失败。`CANCELLED` 也不能证明外部从未执行；资金和门控任务只有经领域状态及原外部身份确认安全后才能取消。
- P0 至少使用相互隔离且有界的 DEVICE 与 FUNDS 执行器；数据库轮询始终是恢复真相，事务提交后的内存唤醒只降低延迟。租约时长必须大于该处理器的网络超时与安全余量，续租也只能由当前 token 条件更新；即使错误配置造成重叠，稳定业务 ID、渠道幂等和本地唯一约束仍必须保证正确性。
- 后台线程从任务中的不可变作用域、动作类型和目标重建受限系统执行上下文，不继承请求 ThreadLocal，也不使用无范围平台绕过；每次执行重新校验当前聚合状态、幂等身份和资金/设备安全闸门，不能把原发起人的旧权限当作当前授权。生产者按“领域聚合锁 → 插入任务”，领取只碰任务；结果事务遵守既有领域锁序并最后更新任务/尝试，禁止长持 task 锁再反向申请钱包、机构账户或设备聚合锁。
- **I-036 接口落实补充**：平台人工恢复只允许锁定原 `task_uid`，校验 `expectedVersion`、当前恰为 `BLOCKED`、无有效租约且操作者已显式确认原因排除；成功后保留原 `task_key`、目标、执行快照和外部业务 ID，递增 `wake_version`、清除阻断投影并改回可立即领取的 `PENDING`。该事务只改变任务调度状态并写审计，不修改领域对象，也不创建替代任务；执行器随后仍须重新检查当前领域和渠道事实。
- **I-041～I-045 接口落实补充**：`CONFIRM_EDGE_EVENT` 的不可变任务快照保存稳定 `confirmation_uid`、原 `event_uid`、原规范摘要、结果种类和安全业务引用；只有可信 `BUSINESS_CONFIRMATION_RECEIPT` 归并后才能 `DONE`，OneNet `code=0` 仅写 attempt。`PROVIDE_PHOTO_UPLOAD_GRANT` 快照只保存原作业、固定槽位和授权前缀，不保存 `TmpSecretId/TmpSecretKey/Token`；凭证在每次实际发送时即时生成。确认回执不再创建回执确认任务，照片补授权也不写 `dev_device_command`，两者均使用 DEVICE 执行通道和既有任务租约恢复。

### D-028 不可变操作审计

**已确认：`ops_audit_log` 只证明操作者、作用域、入口、请求及系统是否接受该敏感动作；具体修订、资金变化、设备执行和微信结果继续由各自强类型事实拥有。**

| 表 | M0 核心字段与约束 |
|---|---|
| `ops_audit_log` | `id`、全局唯一 `audit_uid`、每次请求唯一 `request_uid`、可空稳定业务 `operation_uid`、仅 `SUCCEEDED` 时等于业务操作号的可空生成列 `succeeded_operation_uid`、作用域、`PLATFORM_ADMIN/STAFF_ACCOUNT/SYSTEM/UNAUTHENTICATED` 分型主体、非敏感主体展示快照、稳定动作码、主要目标类型/键、`WEB/MINIAPP_MANAGEMENT/SYSTEM_TASK/SECURITY_ENTRY`、`SUCCEEDED/DENIED/FAILED`、可空会话 UID、关联/因果 ID、可空 IP、User-Agent 摘要、可空原因及脱敏变更摘要、发生时间；唯一 `request_uid` 和唯一 `succeeded_operation_uid`，插入后不可修改或删除。 |

- 平台管理员、工作人员和系统主体分别使用强类型外键或稳定系统主体码；尚未认证的登录失败没有可信账号外键，只允许保存后端生成的请求 UUID、入口、作用域和被声明登录名的摘要。操作者作用域与目标作用域分开解释，平台管理员跨租户处置不会被伪造成租户成员。
- 同步敏感修改成功时，业务事实和一条 `SUCCEEDED` 审计在同一事务提交；已成功的同一 `operation_uid` 重试返回原结果，不重复制造操作记录。`DENIED/FAILED` 不占用成功操作唯一槽，调用方可以用新的 `request_uid`、原业务 `operation_uid` 重新通过当前授权和前置条件；每次失败尝试仍各自留痕。异步设备命令、查单、撤销或重试的审计成功只表示系统已经接受请求并创建了可靠任务，实际执行及渠道结果仍分别看任务尝试、设备事件和微信观察。
- 被权限或前置条件拒绝的安全相关请求可以追加 `DENIED`；事务异常后的 `FAILED` 只记录本次请求失败，绝不能暗示业务事实已发生。签名失败仍不冒充任何已认证操作者，其请求内容只做脱敏安全聚合。
- 余额人工调整、订单修订、审核决定、配置版本、平台闸门恢复等专用事实保存权威前后值和业务结果；审计通过强类型事实中的 `audit_id` 或自身目标导航关联。`safe_change_summary` 只辅助展示没有专用命令事实的普通主数据变更，不能用于余额重算、状态恢复或重放。
- M0 至少覆盖平台代操作、配置/价格修改、员工禁用及授权、审核/纠错、用户冻结、余额人工调整、隔离/告警确认、渠道前终止、查单/任务恢复、微信允许的撤销、平台补资恢复和对账人工动作。完整手机号、OpenID、Token、完整密钥、原始报文和未脱敏 URL 一律不得进入审计；I-012 的 AppSecret 回显审计只记录统一脱敏值和操作事实。
- operations 查询端口按记录作用域再次授权：平台权限可以跨租户，租户人员只能本租户，机构人员只能获授权机构；IP 等安全字段另需更窄权限。不能依赖已排除的 MyBatis 租户拦截器兜底。
- **I-037 接口落实补充**：审计列表采用不可变时间线游标，只返回安全摘要并按固化作用域过滤；普通租户/机构查询不返回 IP、User-Agent 或会话安全字段，平台受限详情才可读取这些字段。P0 不开放审计修改、删除、重放或批量导出接口。

### D-029 持续问题的聚合告警

**已确认：`ops_alert` 是可更新的运营投影；同一持续问题只保留一条活动告警，人员确认与真实恢复完全分离，任何业务判断都不得读取告警状态。**

| 表 | M0 核心字段与约束 |
|---|---|
| `dev_device_fault_event`（对 D-008 的必要补充，由 device 写入） | `id`、全局唯一 `fault_uid`、机构作用域、部署和可空投口、组件类型/稳定故障码、`DEGRADED/BUSINESS_BLOCKING/SAFETY_BLOCKING` 影响快照、由部署/投口哨兵/组件类型/故障码形成的 `fault_key`、仅 `OPEN` 时等于故障键的可空生成列 `active_fault_key`、`OPEN/RECOVERED`、不可变首次来源证据、首次/最近发现时间、发现次数、可空恢复来源/方式/审计、恢复时间和锁版本；唯一 `active_fault_key`，同一故障恢复后复发必须创建新 `fault_uid`。 |
| `ops_alert` | `id`、全局唯一 `alert_uid`、作用域、告警码/类别、当前及历史最高严重级、`DOMAIN_FACT/TECHNICAL_CONDITION` 来源种类、来源类型/稳定键、`aggregation_key BINARY(32)`、仅 `OPEN` 时等于聚合键的可空生成列 `active_aggregation_key`、`OPEN/RESOLVED`、首次/最近发现时间、发现次数、脱敏展示参数、可空确认时间/审计、恢复时间、时间列和锁版本；唯一 `(source_kind, source_type, source_key)` 和唯一 `active_aggregation_key`。 |

- device 在更新当前 runtime state 的同一事务创建、延续或恢复 `dev_device_fault_event`；普通健康观察可以恢复允许自动恢复的故障，门状态未知、安全锁存等严重事件只有完成原设备安全恢复用例才能恢复。该事件不记录用户违规、投递/清运认定或满溢；这些仍由各自领域事实拥有。
- 满溢、设备持续故障、平台资金不足和对账问题分别以 `rec_fullness_event`、`dev_device_fault_event`、`fund_payout_gate_event`、`ops_reconciliation_issue` 的稳定身份作为来源。任务/inbox 积压、签名失败等没有可信领域行的技术条件，由检测器在“正常 → 异常”时生成新的技术事件键，不能伪造 inbox ID 或业务单号。
- 同一持续事件重复检测只条件更新最近发现时间、次数、当前/最高严重级和安全展示参数；`active_aggregation_key` 保证并发下最多一条活动告警。解决后该行不再打开，同类问题再次发生创建带新事件身份的新告警，历史仍保留。
- 人员“已确认”只在同一事务写审计并更新确认投影，不会恢复满溢、开启设备或打开出款闸门。领域告警只能在来源模块已经提交恢复事实后关闭；技术告警只能在检测器重新验证积压或攻击条件消失后关闭。`ops_alert` 不参与开门、出款、冻结释放、账户计算或设备恢复。
- M0 至少持久发现并在 Web 展示：inbox/任务积压或持续失败、设备业务确认超时、门/称重/本地写入阻断、充值已支付待入账、提现超过 30 分钟未结算或渠道冲突、`NOT_ENOUGH` 平台暂停和每日对账差异；当前机构工作人员小程序只读显示本机构精简告警。平台或无法解析作用域的安全/商户告警只向平台展示。
- M0 直接以一条有权限可查的 `OPEN` 告警表示站内可见，不建立固定常量的通知阶段或永远为空的最近通知时间。逐接收人未读、订阅消息记录、10/30 分钟升级、24/48 小时满溢通知以及短信/电话属于 M1；届时以前向迁移增加接收人/投递事实并创建可靠任务，发送结果写任务尝试，不能只更新一个阶段字段就宣称送达。
- **I-038 接口落实补充**：Web 可按授权作用域读取告警，并以 `expectedVersion` 确认当前 `OPEN` 告警；确认只更新人员已查看投影和审计，不关闭来源、不改变严重级，也不阻止同一持续来源继续增加发现次数。工作人员小程序只读当前机构告警，不开放确认；P0 不提供批量确认、推送升级或人工解决接口。

### D-030 每日交易对账、差异与处置轨迹

**已确认：每日对账只复用现有渠道观察和幂等领域用例收敛确定结果；明确可修复的遗漏直接补齐，不确定或矛盾才建立 issue，任何对账表都不能自行写余额、机构额度或微信终态。**

| 表 | M0 核心字段与约束 |
|---|---|
| `ops_reconciliation_run` | `id`、全局唯一 `run_uid`、固定 `scope_kind=PLATFORM`、`WECHAT_DAILY_TRADE`、系统商户、Asia/Shanghai 渠道业务日期、UTC 快照截止时间、唯一协调任务、`CREATED/RUNNING/COMPLETED`、开始/最近进度/完成时间、已检查充值/提现数量、确定性自动修复数量、完成时发现的问题数量、创建时间和锁版本；唯一 `(run_type, merchant_profile_id, channel_business_date)`。 |
| `ops_reconciliation_issue` | `id`、全局唯一 `issue_uid`、作用域、问题码/严重级、目标类型/业务键、`dedupe_key BINARY(32)`、仅未解决时非空的 `active_dedupe_key`、`UNRESOLVED/RESOLVED`、首次/最近发现 run 和时间、发现次数、不可变初始证据摘要/脱敏摘要、可空最近人工已处理时间/审计、系统验证恢复时间、时间列和锁版本；唯一 `active_dedupe_key`。 |
| `ops_reconciliation_action` | `id`、全局唯一 `action_uid`、全局唯一稳定 ASCII `action_key`、不可变作用域、可空 issue、可空 run、`AUTO_REPAIR_APPLIED/NOTE_ADDED/MARK_HANDLED/CHANNEL_QUERY_REQUESTED/CONTROLLED_RETRY_REQUESTED/RESOLUTION_VERIFIED`、`RECONCILIATION_RUN/HUMAN_AUDIT/TASK_ATTEMPT` 来源及 run/审计/任务尝试三选一、可空可靠任务、可空权威结果事实类型/键、脱敏备注或结果摘要和发生时间；只追加，自动修复不要求先制造 issue。 |

- 每个系统商户每天建立一个确定性 run 和唯一可靠任务；服务重启补建遗漏日期。run 的业务日期按 Asia/Shanghai 解释，所有执行时间仍按 UTC 保存。M0 扫描该日候选交易，并再次检查微信 30 天可查窗口内尚未收敛、充值已支付待入账和提现终态未结算的记录；任务暂时失败只写尝试并恢复原 run，不另建第二轮。
- 对账覆盖充值单/支付观察/机构充值明细，提现单/转账观察/活动占位，以及用户与机构两侧资金明细及账户投影。`COMPLETED` 只表示在该快照截止点完成扫描，不代表零异常；run 只保存非金额计数，不复制余额、渠道状态或账本累计值。
- 自动修复只允许在“完整原子结果尚未发生，且聚合仍精确处于原用例允许的前置状态”时调用 D-023/D-025 的同一幂等领域用例：充值必须仍为 `PAID_PENDING_POST` 且不存在充值入账明细；提现必须已有单一可信终态、仍处于对应待结算状态、活动槽位和双侧冻结完整，且用户与机构任一侧都不存在该阶段 `FINAL` 明细。若已经出现单侧 FINAL、槽位/冻结/投影矛盾或部分结果，不得尝试“只补另一半”，必须建立 issue 停止自动资金修改。
- 成功修复事务创建原本应有的全部钱包/机构明细和投影，同时追加唯一 `AUTO_REPAIR_APPLIED` action 引用这些权威事实；其 `action_key` 固定为运行、目标聚合和修复阶段的规范组合，重复 run/任务最多返回原 action 或业务 no-op。自动修复成功不先制造 issue，action 计数和摘要也不能替代原资金事实。
- run 是系统商户级平台记录；issue 和 action 必须按实际差异/目标聚合固化 `PLATFORM/TENANT/ORGANIZATION/UNRESOLVED` 作用域。自动修复 action 从被修复聚合复制并在同一事务校验作用域；挂接 issue 时必须与 issue 作用域完全一致，平台级动作才允许 `PLATFORM`。因此租户/机构查询可直接安全过滤，不能依赖无外键的目标键临时反查后再决定是否泄露。
- 金额/身份矛盾、同一渠道单相反终态、未知微信状态、证据不足或调用原用例后仍不一致时，停止自动资金修改，以稳定差异键创建或刷新一个 issue，并创建/刷新其聚合告警。同一 run 重试不重复累计发现次数，初始差异证据永不覆盖。
- issue 只允许 `UNRESOLVED -> RESOLVED`，解决后永不重新打开；同一差异以后复发时，以原 `dedupe_key` 重新占用活动槽但创建新的 `issue_uid` 和新告警来源身份。这样保留每次不一致的独立发生/恢复历史，也不会与告警来源永久唯一约束冲突。
- 人员只能通过重新授权的用例追加备注、标记已处理、发起原单查单或受控重试；每次同时写审计和只追加 action。`handled_at` 与 `RESOLVED` 分离，“已处理”不等于账已一致；只有系统重新读取权威业务表和渠道观察并确认矛盾消失，才能在同一事务追加 `RESOLUTION_VERIFIED`、解决 issue 并关闭告警。
- `ops_reconciliation_action` 不提供编辑资金明细、修改提现金额/收款人、设置微信成功/失败或直接调账的动作。异步查询看可靠任务及尝试，渠道结果看 observation，资金结果看原账本明细；action 只保留对账为何发起或验证了某项处置的轨迹。
- M0 只做充值、提现和双方资金明细的每日交易级核对及确定性遗漏补齐；不读取微信基本/运营账户实际余额，不做完整资金账单归档、长期财务导出、通用规则引擎或自动补资恢复。这些能力进入 M1，不能用本批表中的计数伪装为完整财务对账。
- **I-039 接口落实补充**：M0 模式固定为 `INTERNAL_TRANSACTION_CONVERGENCE`。运行由确定性每日任务建立，平台只读运行，不能从管理端重复创建同日 run；人工只能在 `UNRESOLVED` 问题上追加备注、已处理标记、原单查证或满足安全前置条件的受控重试，解决态只读。受控重试仍复用原可靠任务和外部 ID；“已处理”不等于“已解决”，也不能局部补一侧资金。微信官方交易/资金账单文件申请、下载、哈希校验和归档明确留到 M1。
- **I-040 接口落实补充**：最小运营概览不新增累计统计表、缓存余额或人工可修正指标。operations 只能通过各模块公开查询端口读取权威表；同一请求在专用只读 `REPEATABLE READ` 事务中建立一致快照，所有业务写事务继续遵守 D-036 的 `READ COMMITTED`。返回 `asOf`，期间指标与当前投影分开，历史期间的当前认定口径允许随后续投递纠错变化。
