# F-06｜目标数据库 V6～V10 funds、operations 验证矩阵

> 范围：`V6__funds.sql`～`V10__permission_reference_data.sql`，前置为已经完成的
> V1～V5。
>
> 事实来源：D-021～D-045、详细设计 01 和 F-06。
>
> 结论：V6 准确创建 **20 张 funds 表**，V7 准确创建
> **9 张 operations 表**；V1～V10 合计 **83 张表**。

## 1. 83 表覆盖与约定

完整逐表覆盖由三份连续矩阵组成：

| 迁移范围 | 表数 | 验证矩阵 |
|---|---:|---|
| V1～V4 identity/device | 30 | [F-04 V1～V4 矩阵](f-04-v1-v4-verification-matrix.md) |
| V5 recycling | 24 | [F-05 V5 矩阵](f-05-v5-recycling-verification-matrix.md) |
| V6～V7 funds/operations | 29 | 本文第 2、3 节 |
| **合计** | **83** | 不含已删除的云端投递周期候选表 |

- 所有机构级表都直接以 `tenant_id + organization_id` 引用机构根。
- 所有外键子列都由显式 DDL 索引的左前缀覆盖。
- `A` 表示只追加事实，`S` 表示当前槽位，`O` 表示一次补齐或单向收敛，
  `P` 表示受保护当前投影，`R` 表示运行时只读。
- `A` 无运行时 `UPDATE/DELETE`；`S` 只允许 `SELECT/INSERT/DELETE`；
  `O/P` 只允许更新本表矩阵列出的投影列。
- `CHECK` 证明本行形状，外键证明身份与作用域；合法状态迁移、严格序号递增、
  双账本原子提交和外部渠道真实性仍由条件 SQL、锁序和事务验收保证。

## 2. V6 funds：20 张表

| 表 / 写类 | 作用域、稳定身份与强关系 | UQ / CHECK / 首批索引 | 运行账号可更新列 |
|---|---|---|---|
| `fund_wechat_merchant_profile` / P | funds 所有；平台级普通商户；公开 UUID；只以稳定 `id + mchid` 供渠道单引用 | 商户号唯一；普通商户、场景与非秘密配置形状；可变配置不进入父候选键；状态索引 | `status, scene_id, report_type, report_content, transfer_page_style, non_secret_config_ref, lock_version, updated_at` |
| `fund_miniapp_merchant_binding` / P | funds 所有；机构、小程序、AppID、商户强关系 | 小程序和 AppID 各唯一；验证/失效时间形状；商户状态与机构索引 | `status, disabled_at, lock_version, updated_at` |
| `fund_payout_gate` / P | funds 所有；以商户为主键 | 当前暂停事件必须是同商户 `PAUSED` 事件；状态索引 | `gate_state, current_pause_event_id, paused_at, lock_version, updated_at` |
| `fund_organization_withdraw_config` / A | funds 所有；机构版本 | 机构+版本唯一；`10 <= min <= max <= hard <= 20000`，M0 免审阈值为 0；版本倒序 | — |
| `fund_organization_withdraw_config_head` / P | funds 所有；以机构为主键 | 当前配置必须属于同机构且版本一致 | `current_config_id, current_version_no, lock_version, switched_at, updated_at` |
| `fund_organization_wallet_entry_counter` / P | funds 所有；以机构为主键 | 非负提交可见序号和锁版本 | `last_visibility_sequence_no, lock_version, updated_at` |
| `fund_user_wallet` / P | funds 所有；机构用户一对一 | 可用余额可带符号，提现冻结和末序号非负；投递闸及触发明细形状 | `available_balance_cent, frozen_withdrawal_cent, last_entry_sequence_no, delivery_gate_state, delivery_gate_threshold_snapshot_cent, delivery_gate_trigger_entry_id, delivery_gate_latched_at, lock_version, updated_at` |
| `fund_organization_payout_account` / P | funds 所有；机构一对一 | 可用额度和提现冻结均非负 | `available_payout_cent, frozen_withdrawal_cent, lock_version, updated_at` |
| `fund_recharge_order` / P | funds 所有；充值单号 | 机构/创建人强关系；6000 ppm 向上取整手续费及净额公式；机构状态时间和到期扫描 | `business_state, paid_at, posted_at, closed_at, lock_version, updated_at` |
| `fund_wechat_payment` / P | funds 所有；充值一对一、支付 UUID、商户内 `out_trade_no` | 商户/小程序绑定快照强关系；微信支付单号可空唯一；请求快照和渠道投影形状 | `code_url, transaction_id, channel_state, last_api_error_code, channel_updated_at, lock_version, updated_at` |
| `fund_withdrawal_order` / P | funds 所有；提现单号 | 用户、钱包、机构账户、配置和收款身份复合强关系；正金额、配置快照、业务/渠道边界时间形状；长时间未结算只能在渠道边界 30 分钟后标记并可随渠道终态保留 | `business_state, negative_balance_pause, post_boundary_risk, pre_channel_block_reason, channel_boundary_at, long_unsettled_at, reviewed_at, channel_terminal_at, ended_at, lock_version, updated_at` |
| `fund_active_withdrawal` / S | funds 所有；以钱包为主键，提现单唯一 | 钱包与提现单必须同机构 | — |
| `fund_wallet_adjustment` / A | funds 所有；调整 UUID | 钱包、分型操作者和同作用域成功审计强关系；非零差额及前后值代数 | — |
| `fund_withdrawal_review` / A | funds 所有；审核 UUID，提现一对一 | 分型审核人和同作用域成功审计强关系；决定与人员形状 | — |
| `fund_wechat_transfer` / P | funds 所有；提现一对一、转账 UUID、`out_bill_no`；商户稳定身份与 `mchid` 强关系 | 绑定/收款快照强关系；场景、报备和页面样式由创建事务复制为不可变快照，不反向锁住当前商户配置；微信单号可空唯一；请求、渠道和内部终态分离 | `transfer_bill_no, channel_state, terminal_classification, package_info, last_api_error_code, terminal_fail_reason, state_conflict, channel_updated_at, terminal_at, lock_version, updated_at` |
| `fund_user_wallet_entry` / A | funds 所有；明细 UUID、钱包内序号、机构可见序号 | 投递修订/提现/调整来源 XOR；提现 `FREEZE/FINAL` 唯一；可用与冻结前后值代数 | — |
| `fund_organization_payout_entry` / A | funds 所有；明细 UUID | 充值/提现来源 XOR；充值与提现阶段唯一；双项余额代数和充值费用快照 | — |
| `fund_wechat_payment_observation` / A | funds 所有；观察 UUID | inbox/attempt 来源 XOR 且同作用域；每可信来源最多一条；原始渠道状态可扩展 | — |
| `fund_wechat_transfer_observation` / A | funds 所有；观察 UUID | inbox/attempt 来源 XOR 且同作用域；每可信来源最多一条；原始渠道状态可扩展；从精确 `api_error_code` 生成只读闸门触发分类 | — |
| `fund_payout_gate_event` / A | funds 所有；事件 UUID | PAUSED 同时复合引用同商户 transfer 和其 `NOT_ENOUGH` 分类观察；恢复唯一引用原暂停事件；事件列组形状 | — |

资金双账本分别由 `fund_user_wallet_entry` 和
`fund_organization_payout_entry` 拥有事实；钱包/机构账户行只是受保护投影。
两类明细都检查 `before + delta = after`，提现冻结/结算分别用
`FREEZE/FINAL` 唯一阶段保证至多一次。充值费用固定为
`ceil(gross_cent * 6000 / 1000000)`，未知微信原始状态只保存为观察，
不能被数据库枚举拒绝或擅自映射为业务终态。

transfer 的场景、报备内容和确认页样式仍是创建后不可变的请求快照，但只由创建事务
从锁内商户配置复制并校验；父表候选键不包含这些以后可更新的当前配置。
`fund_wechat_transfer_observation.payout_gate_trigger_code` 与
`fund_payout_gate_event.triggering_api_error_code` 均为数据库生成列。后者对
`PAUSED` 固定生成 `NOT_ENOUGH`，复合 FK 因而同时证明 observation 身份、
transfer 身份和精确错误分类，另一个复合 FK 证明 transfer 属于事件商户。

## 3. V7 operations：9 张表

| 表 / 写类 | 作用域、稳定身份与强关系 | UQ / CHECK / 首批索引 | 运行账号可更新列 |
|---|---|---|---|
| `ops_inbox_message` / P | operations 所有；inbox UUID；平台/租户/机构/未解析作用域 | 来源命名空间+主体+外部消息 ID 唯一；消息摘要、认证引用和处理形状；待处理及作用域时间索引 | `processing_state, last_received_at, delivery_count, processed_at, lock_version, updated_at` |
| `ops_audit_log` / A | operations 所有；审计/请求 UUID | 分型主体强关系；仅成功时占用 operation 生成槽；作用域、动作和操作者时间线 | — |
| `ops_message_quarantine` / O | operations 所有；隔离 UUID、规范去重键 | 可空冲突 inbox/确认审计；作用域、确认和非负版本形状 | `last_seen_at, discovery_count, status, acknowledged_audit_id, acknowledged_at, lock_version, updated_at` |
| `ops_reliable_task` / P | operations 所有；任务 UUID、永久 `task_key` | inbox/设备命令最多一个任务；租约三列成组、wake 单调、终态清空调度；`claimable_at` 领取索引 | `state, next_run_at, lease_token, lease_worker, lease_until, attempt_sequence, consecutive_failure_count, wake_version, handled_wake_version, completed_at, blocked_reason_code, blocked_diagnostic, lock_version, updated_at` |
| `ops_task_attempt` / O | operations 所有；attempt UUID、任务内尝试序号、租约 token | 调用边界、接管和技术结果各从空补一次；技术结果不冒充业务终态 | `lease_until, external_call_may_have_started_at, reclaimed_at, result_recorded_at, action_kind, technical_result, request_sha256, response_sha256, http_status, external_api_error_code, duration_ms, redacted_diagnostic` |
| `ops_alert` / O | operations 所有；告警 UUID、永久来源键 | 活动聚合生成键唯一；确认不等于恢复，解决后不 reopen；作用域活动严重级索引 | `current_severity, highest_severity, last_seen_at, discovery_count, safe_display_parameters, acknowledged_at, acknowledged_audit_id, status, resolved_at, lock_version, updated_at` |
| `ops_reconciliation_run` / P | operations 所有；平台作用域、run UUID | 商户+渠道业务日和协调任务唯一；运行状态、计数及时间形状 | `state, started_at, last_progress_at, completed_at, checked_recharge_count, checked_withdrawal_count, deterministic_repair_count, issue_count_at_completion, lock_version, updated_at` |
| `ops_reconciliation_issue` / O | operations 所有；issue UUID | 未解决 dedupe 生成键唯一；首次/最近 run、处理审计强关系；解决后不 reopen | `severity, latest_seen_run_id, last_seen_at, discovery_count, state, last_handled_at, last_handled_audit_id, system_verified_resolved_at, lock_version, updated_at` |
| `ops_reconciliation_action` / A | operations 所有；action UUID、永久 action key | issue 及 run/audit/attempt 来源 XOR；作用域一致；只记录处置轨迹，不直接修改资金 | — |

## 4. V8：后置关系闭合

V8 只有 `ALTER TABLE`，不建表、不写数据、不关闭外键检查。为后置复合外键增加
8 个必要候选键和 5 个确定性生成列，并闭合以下 **29 个外键**：

| 子表 → 父表 | 闭合内容 |
|---|---|
| `dev_delivery_session → rec_organization_delivery_config` | 配置 ID、摘要、开门余额阈值和人工认定上限快照一致 |
| `dev_delivery_session → rec_bag` | 机构、袋 ID 和大小写敏感袋码快照一致 |
| `dev_device_occupancy → rec_clean_operation` | 整机占位与清运操作的机构、部署一致 |
| `dev_device_command → rec_clean_operation / rec_fullness_detection / rec_port_baseline_measurement` | 三类后置命令目标的机构、部署一致 |
| `dev_physical_result → rec_clean_operation / rec_fullness_sample / rec_port_baseline_measurement` | 三类后置物理结果目标强类型引用；可携带的部署/投口同时校验 |
| `fund_miniapp_merchant_binding → iam_organization_miniapp` | 绑定 AppID 必须等于机构小程序 AppID |
| `fund_withdrawal_order → iam_organization_user` | 提现用户、小程序和 OpenID 收款快照属于同一注册身份 |
| `dev_edge_event → ops_inbox_message` | inbox 身份及机构作用域双重外键 |
| `dev_device_fault_event → ops_audit_log` | 恢复审计身份及机构作用域双重外键 |
| `fund_user_wallet → fund_user_wallet_entry` | 停投触发明细必须属于本钱包 |
| `fund_payout_gate → fund_payout_gate_event` | 当前暂停指针必须引用同商户 `PAUSED` 事件 |
| `fund_wallet_adjustment / fund_withdrawal_review → ops_audit_log` | 成功审计身份及机构作用域双重外键 |
| 两张微信 observation → `ops_inbox_message / ops_task_attempt` | inbox/attempt 各有身份和作用域外键；表内 CHECK 保证来源 XOR |

以上每个新增子外键都有显式左前缀索引。父表当前指针与子事实形成的合法循环仍按
“先插允许空指针的父行 → 插子事实 → 同事务回填父指针并推进状态”构造。

## 5. V9：仅两类不可变保护

V9 只创建两个 `BEFORE UPDATE` 触发器，稳定定义者固定为
`'ecobin_trigger_definer'@'%'`：

1. `trg_iam_miniapp_activation_immutable`：激活后禁止改变激活时间、AppID、
   租户或机构；激活前仍允许纠正配置。
2. `trg_iam_org_user_registration_immutable`：禁止改变机构用户的租户、机构、
   小程序、OpenID、注册时间和来源部署。

MySQL 8.4 实测表明，该锁定定义者除 schema 级 `TRIGGER` 外，还必须具备触发器
读取 `OLD/NEW` 所涉列的精确列级 `SELECT`。H-02 应在同一个受控迁移作业中：

1. 先创建锁定定义者并授予目标 schema 的 `TRIGGER`；
2. 由 schema owner 安装 V1～V8；
3. 授予下面的精确列读取权限；
4. 同一迁移作业继续安装 V9～V10；
5. 应用启动前执行触发器正反例。

把 `${TARGET_SCHEMA}` 替换为目标库名后，最小定义者授权为：

```sql
GRANT TRIGGER ON `${TARGET_SCHEMA}`.*
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    activated_at, appid, tenant_id, organization_id
) ON `${TARGET_SCHEMA}`.iam_organization_miniapp
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    tenant_id, organization_id, organization_miniapp_id, openid,
    registered_at, registered_via_deployment_id
) ON `${TARGET_SCHEMA}`.iam_organization_user
    TO 'ecobin_trigger_definer'@'%';
```

定义者必须 `ACCOUNT LOCK`，不得拥有密码注入、交互登录、表级 `SELECT`、业务 DML、
DDL、`GRANT OPTION` 或旧库权限。

## 6. V10：环境无关权限目录

V10 只向 `iam_permission_definition` 插入 **71 行**：

- `organization-manager.manage` 仅有 `TENANT` 一种作用域；
- 其余 35 个权限码各有 `TENANT`、`ORGANIZATION` 两种作用域，共 70 行；
- 包含 identity、device、delivery、clean、wallet、funds、audit、alert、
  reconciliation 和 statistics 能力；
- 不包含平台固定用例伪造权限，不包含租户、机构、人员、AppID、商户号、设备、
  袋、余额、订单、账号或秘密。

V10 已应用后，新增权限只能使用新的前向迁移。

## 7. 给 H-02 的最小权限执行矩阵

| 身份 | 必须拥有 | 明确禁止 |
|---|---|---|
| 实例初始化/恢复管理员 | 建库、创建/锁定账号、初始授权和灾难恢复；只在环境供应边界使用 | 进入应用配置、日常 Flyway 或普通运维会话 |
| `ecobin_schema_owner` | 目标 schema 的 V1～V10 DDL/DML、Flyway history；目标 MySQL 8.4 创建指定定义者触发器所需全局 `SET_ANY_DEFINER` | 进入运行容器、访问旧库、长期在线使用、`GRANT OPTION`、`SUPER` |
| `ecobin_trigger_definer` | 第 5 节三个最小授权；账号锁定 | 交互登录、业务 DML、全表读取、DDL、GRANT、旧库 |
| `ecobin_app` | 83 表所需 `SELECT`；除权限目录外的正式用例 `INSERT`；按写类和列矩阵授予更新/删除；只读 Flyway history | schema 级 `INSERT/UPDATE/DELETE`，任何整表 `UPDATE`，DDL、REFERENCES、TRIGGER、GRANT、事实删除、旧库 |
| `ecobin_backup` | 显式 83 表只读；备份工具确实需要的 `SHOW VIEW`；InnoDB 使用 single-transaction | 业务 DML、DDL、TRIGGER、EVENT、GRANT、旧库；不得为方便直接给管理员角色 |

F-07 用真实 Flyway + schema owner 补充验证发现：MySQL 8.4 开启 binary log 时，
仅有 `SET_ANY_DEFINER` 仍会以 1419 拒绝 V9 触发器。环境供应应显式启用受控的
`log_bin_trust_function_creators=ON`，不能为迁移方便给 schema owner 增加 `SUPER`。

`ecobin_app` 的授权生成规则：

| 写类 | 允许生成的 GRANT |
|---|---|
| R | `SELECT`；`iam_permission_definition` 不得 `INSERT/UPDATE/DELETE` |
| A | `SELECT, INSERT`；不得 `UPDATE/DELETE` |
| S | `SELECT, INSERT, DELETE`；仅四张当前槽位表，不得 `UPDATE` |
| O / P | `SELECT, INSERT`，再按三份 83 表矩阵的“运行账号可更新列”生成列级 `UPDATE (...)` |

本文件第 2、3 节给出了 F-06 新增 29 表的完整更新列；V1～V5 表继续以 F-04、
F-05 矩阵和 D-035 冻结写类为准。H-02 生成授权后必须保存脱敏
`SHOW GRANTS`，同时验证：

- 正例：正式 use case 可插入事实、条件更新投影、删除当前槽位、只读 Flyway history；
- 负例：应用账号无法 DDL、GRANT、管理触发器、改 A 类事实、整表更新、
  删除历史证据或访问旧库；
- 任何激活角色也不得给列级保护表叠加更高层级 `UPDATE/DELETE`。

F-06 不创建真实环境账号、不执行正式 GRANT、不保存密码或凭证；这些操作属于 H-02。

## 8. 自动验证证据

运行：

```powershell
.\tools\database\verify-f06-migrations.ps1
```

验证器固定官方 MySQL 8.4 镜像 ID，创建两个独立空库，严格安装 V1～V10，
并在 `finally` 中删除临时容器。覆盖：

- V6/V7 表清单准确为 20/9，V1～V10 合计 83；
- V6/V7 无 DML，V8 只有后置 `ALTER`，V9 只有两类触发器，
  V10 只有权限目录插入；
- 两套空库结构完全一致，316 个外键全部由显式索引左前缀覆盖；
- 24 张 F-06 机构级表全部直接引用机构根；
- 20 个非法配置、跨机构、账本代数、来源 XOR、提现错误长时间未结算、
  暂停跨商户/非 `NOT_ENOUGH` 证据、暂停指针、任务租约、审计主体、
  隔离确认、告警/对账解决和不可变身份反例全部被拒绝；
- 激活前纠正、普通用户资料更新、历史 transfer 后商户配置更新且快照不变、
  渠道终态保留既有长时间未结算标记、未知微信状态保存、同商户
  `NOT_ENOUGH` 暂停、双账本和任务领取投影正例通过；
- 向非空库重复执行 V6、V8 或 V10 会失败。

2026-07-25 本地证据：

| 项目 | 结果 |
|---|---|
| MySQL | `8.4.10` |
| 镜像 ID | `sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6` |
| 对象计数 | `83 tables / 316 FK / 315 UQ / 414 CHECK / 327 non-unique indexes / 2 triggers` |
| funds / operations | `20 / 9` |
| 权限定义 | `71` |
| F-06 直接机构 FK | `24 / 24` |
| 两库结构 SHA-256 | `fb590143b6989de5f45bd3cbe1e5a1d061878299ee7f7952f7acb8ba59e505fa` |
| 数据正反例 | `20` 个负例；8 类关键正例 |
| F-05 回归 | 通过；指纹 `af3684aca14e082afa624a425f2d7aeca675b39dfd8300e66eb5b3f744c40ca7` |
| F-04 回归 | 通过；指纹 `cef82b4a9772819a1fb01e37bec3b88cd4cecc7ec9231ec55815a03ee42568f7` |
| Java 回归 | JDK `21.0.10`、23 suites、82 tests、0 failures/errors/skips |
| 结果 | 两库结构相同，全部自动检查通过 |
