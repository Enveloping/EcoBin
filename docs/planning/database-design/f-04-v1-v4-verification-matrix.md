# F-04｜目标数据库 V1～V4 验证矩阵

> 范围：只覆盖 `V1__p0_epoch_and_iam_core.sql` 至
> `V4__device_operations_and_evidence.sql`。
>
> 事实来源：D-006～D-015、D-031～D-035、D-041～D-045 和 F-04。
>
> 结论：四个版本共创建 **30 张表**，其中 identity 14 张、device 16 张；
> 不创建 `dev_delivery_cycle`，不插入环境或业务实例数据。

## 1. 读取说明

- “作用域 FK”表示使用 `tenant_id + organization_id + ...` 的复合外键；
- `UQ` 包括永久业务身份、候选键和活动生成槽；
- `CK` 是由 MySQL 8.4 实际执行的命名 `CHECK`；
- “后置”只列因父表位于 V5/V7 而必须由 V8 闭合的关系，不表示放弃外键；
- 每个外键子列都有显式左前缀索引，唯一键已覆盖时不重复建立普通索引。

## 2. V1：IAM 核心 8 表

| 表 / 所有者 | PK / 公开键 | 当前可建立的复合 FK | UQ / CK | 首批查询索引 |
|---|---|---|---|---|
| `iam_platform_admin` / identity | `id`；`platform_admin_uid` | 平台级，无作用域 FK | 平台 UUIDv4、平台登录名；规范登录名、启停、计数器与时间 | 启停列表 |
| `iam_tenant` / identity | `id`；`tenant_code` | 租户根 | 租户码；规范租户码、状态、版本与时间 | 平台租户状态列表 |
| `iam_organization` / identity | `id`；`tenant_id + organization_code` | `tenant_id → iam_tenant` | 租户内机构码、`(tenant_id,id)` 候选键；状态、版本与时间 | 租户机构状态列表 |
| `iam_organization_miniapp` / identity | `id`；全局 AppID | 作用域 FK → organization | 全局非空白 AppID、每机构一行、机构候选键；登录启用必须已有激活时间、引用与时间 | 机构小程序配置 |
| `iam_staff_account` / identity | `id`；`staff_account_uid` | 租户 FK → tenant | 工作人员 UUIDv4、全平台工作人员登录名、租户主体生成槽、租户候选键；账号类型、规范登录名、启停与版本 | 租户工作人员列表 |
| `iam_organization_staff_membership` / identity | `id` | 作用域 FK → organization；租户复合 FK → staff | 机构+员工唯一、机构候选键；负责人、启停、版本与时间 | 员工→机构；机构→负责人 |
| `iam_permission_definition` / identity | `id`；`permission_code + scope_kind` | 平台目录 | 权限码+作用域、`(id,scope_kind)` 候选键；规范权限码、作用域与启停 | 作用域权限目录 |
| `iam_staff_permission_grant` / identity | `id` | 租户复合 FK → staff；复合 FK → permission；机构级时作用域 FK → organization/membership | 规范作用域生成键+活动生成槽；TENANT/ORGANIZATION 空值形状、撤销时间 | 工作人员活动授权；机构 FK |

## 3. V2：设备库存、配置与运行投影 9 表

| 表 / 所有者 | PK / 公开键 | 当前可建立的复合 FK | UQ / CK | 首批查询索引 |
|---|---|---|---|---|
| `dev_device_asset` / device | `id`；`hardware_sn` | 平台级资产 | 非空白硬件 SN；投口数、生命周期、报废形状、版本与时间（报废不得早于创建） | 资产生命周期 |
| `dev_device_deployment` / device | `id`；`public_code` | 作用域 FK → organization；单列 FK → asset | `Dp_...` 公开码、机构候选键、资产+部署候选键；生命周期、经营开关、结束形状 | 机构设备列表；资产部署历史 |
| `dev_asset_active_deployment` / device | `asset_id` | 直接作用域 FK → organization；资产+部署强 FK；完整作用域+资产+部署 FK → deployment | 每资产 PK、每部署唯一；当前槽无状态历史 | 机构当前部署 |
| `dev_port` / device | `id`；部署内 `port_no` | 作用域+部署 FK → deployment | 部署+投口号、机构/部署候选键；投口 `1..6` | 部署投口 |
| `dev_config_version` / device | `id`；部署内 `version_no` | 直接作用域 FK；部署 FK；可空租户复合 FK → publisher staff | 部署+版本、机构/部署候选键、完整/MCU 摘要与作业冻结候选键；F-10 当前设备子集精确 u32 边界、版本 JSON 安全整数、设备级负重量阈值、固定 30 秒继续等待 | 部署配置版本倒序 |
| `dev_port_config_snapshot` / device | `id` | 直接作用域 FK；同部署复合 FK → config version 和 port | 配置+投口、session 强引用候选键；F-10 展示名、价格换算及 u32/u16/i32 参数边界 | 机构/部署/投口 |
| `dev_config_application` / device | `id`；`application_uid` | 直接作用域 FK；期望/报告配置三元摘要复合 FK | 应用 UUIDv4、每配置一过程、部署候选键；`EDGE_SAVED/FAILED/APPLIED` 完整三元组、双证明时间、可保留最近失败 | 部署应用状态 |
| `dev_deployment_runtime_state` / device | `deployment_id` | 直接作用域+部署 FK；已应用配置三元组 FK | 部署一对一、机构候选键；F-10 edge/MCU/UART/capability/配置/存储/时钟/待确认事件无损投影 | 机构阻断；全局离线扫描 |
| `dev_port_runtime_state` / device | `port_id` | 直接作用域+部署+投口 FK；V4 增加同投口 pending session FK | 投口一对一、机构/部署候选键；每投口投递门、清运锁推定门态、称重/红外/烟雾无损投影 | 部署投口阻断；待处理 session |

## 4. V3：机构用户、绑定与三类会话 6 表

| 表 / 所有者 | PK / 公开键 | 当前可建立的复合 FK | UQ / CK | 首批查询索引 |
|---|---|---|---|---|
| `iam_organization_user` / identity | `id`；`organization_user_uid` | 直接作用域 FK；作用域+AppID FK → miniapp；可空作用域 FK → 注册来源 deployment | 用户 UUIDv4、AppID+非空白 OpenID、机构+可空 E.164 手机号、机构候选键；冻结、版本与时间 | 机构用户列表；设备注册归因 |
| `iam_organization_user_capability` / identity | `id` | 作用域+用户 FK | 用户+能力码；规范能力码、启停/撤销形状、版本 | 机构活动能力 |
| `iam_staff_miniapp_binding` / identity | `id`；`binding_uid` | 作用域+AppID+用户 FK；租户复合 FK → staff | 绑定 UUID、当前用户槽、当前工作人员+AppID 槽、管理 session 候选键；ACTIVE/REVOKED 时间形状，原因可空 | 员工/AppID 当前绑定；机构用户当前绑定 |
| `iam_platform_login_session` / identity | `id`；`session_uid` | FK → platform admin | session UUID；签发、过期、撤销形状，原因可空 | 主体会话；全局过期清理 |
| `iam_staff_login_session` / identity | `id`；`session_uid` | 租户复合 FK → staff；MINIAPP_MANAGEMENT 复合 FK → binding | session UUID；WEB/管理端字段组、时间与撤销形状，原因可空 | 工作人员会话；绑定会话；过期清理 |
| `iam_organization_user_session` / identity | `id`；`session_uid` | 作用域+AppID+用户 FK | session UUID；签发、过期、撤销形状，原因可空 | 用户会话；过期清理 |

## 5. V4：设备作业和证据 7 表

| 表 / 所有者 | PK / 公开键 | 当前可建立的复合 FK | UQ / CK | 首批查询索引 / 后置 |
|---|---|---|---|---|
| `dev_delivery_session` / device | `id`；`session_uid` | 直接作用域 FK；部署/投口/用户 FK；设备配置三元摘要+阈值+等待和投口 snapshot+单价 FK | session UUIDv4、机构/部署/投口/冻结配置候选键；克数上限、负余额下限、状态/终态/期限 | 设备活动、用户历史、授权超时、结果恢复；V8 后置 delivery config、bag |
| `dev_device_fault_event` / device | `id`；`fault_uid` | 直接作用域+部署、可空投口、恢复 staff FK；首见/恢复事件按固定事件类型复合 FK | fault UUIDv4、活动故障生成槽、不可变首见证据、发现次数；`OPEN/RECOVERED`、恢复来源/方式/审计形状 | 机构活动故障 |
| `dev_device_occupancy` / device | `asset_id` | 当前资产部署复合 FK；DELIVERY 时强 FK → session | 资产 PK、delivery/clean 目标各唯一；DELIVERY/CLEAN 恰一 | 机构当前占位；V8 后置 clean operation |
| `dev_device_command` / device | `id`；`command_uid` | 直接作用域+部署 FK；强 FK → delivery session/config application（含 deployment） | command UUIDv4、命令类型/投递 session 候选键；五类目标恰一、载荷版本、七态与时间字段组严格对应 | 部署命令；五类目标；V8 后置 clean/fullness/baseline |
| `dev_edge_event` / device | `id`；`event_uid` | 直接作用域+部署 FK | event UUIDv4、部署全局序号、来源 inbox 各唯一、类型分支候选键；事件/交付类别与 target 类型成对、时钟组、摘要 | 部署事件时间线、类型时间线；V8 后置 `ops_inbox_message` |
| `dev_device_command_event` / device | `id`；唯一 `edge_event_id` | 直接作用域 FK；类型化 edge event/command FK；投递开始强关联同一 session | 一个公共头一个命令分支；观察类型与原命令类型一致、命令阶段、F-10 `mcuCommandUid` 与符号化 `errorCode`；不保存当前 payload 未提供的 MCU boot/event 或 UART 字段 | 命令时间线 |
| `dev_physical_result` / device | `id`；唯一 `edge_event_id/command_id` | 直接作用域 FK；类型化 edge event/command、报告配置三元组 FK；DELIVERY 同时锁定 session/投口/冻结配置 | 四类目标恰一且非本分支字段全空；每份 measurement 独立保存完整可靠性和 MCU 来源；投递门终态、清运人工确认与推定门态 | 部署/投口结果时间线；V8 后置 clean operation/fullness sample/baseline measurement |

## 6. V8 必须闭合、F-04 不提前建立的关系

F-04 为后续父表预留非空或可空的强类型列，但不建立不存在父表的伪外键：

1. `dev_delivery_session.delivery_config_version_id` 和
   `dev_delivery_session.bag_id` → V5 recycling 配置与袋；
2. `dev_device_occupancy.clean_operation_id` → V5 清运操作；
3. `dev_device_command.clean_operation_id/fullness_detection_id/
   baseline_measurement_id` → V5 对应聚合；
4. `dev_edge_event.source_inbox_id` → V7 `ops_inbox_message`；
5. `dev_physical_result.clean_operation_id/fullness_sample_id/
   baseline_measurement_id` → V5 对应结果根。

这些关系必须由 V8 使用完整作用域和强类型候选键闭合；不得只补裸 ID 外键。

## 6.1 H-02 运行账号列级更新矩阵

下表补齐 D-035 已冻结的 P/O 写类到实际 V1～V4 列。未列出的身份、作用域、稳定
业务键、来源快照、首次证据和创建时间均不得获得 `UPDATE`。四张当前槽位表继续只按
D-035 使用 `SELECT/INSERT/DELETE`；A/R 表没有 `UPDATE/DELETE`。

| 表 / 写类 | `ecobin_app` 可更新列 |
|---|---|
| `iam_platform_admin` / P | `password_hash, display_name, enabled, failed_login_count, locked_until, auth_version, password_changed_at, lock_version, updated_at` |
| `iam_tenant` / P | `enterprise_name, status, contact_name, contact_phone, contact_address, lock_version, updated_at` |
| `iam_organization` / P | `organization_name, status, contact_phone, contact_address, lock_version, updated_at` |
| `iam_organization_miniapp` / P | `appid, display_name, login_enabled, secret_ref, activated_at, lock_version, configured_at, updated_at`；V9 继续阻断激活后 AppID/作用域/激活时间变化 |
| `iam_staff_account` / P | `password_hash, display_name, contact_phone, enabled, failed_login_count, locked_until, auth_version, password_changed_at, lock_version, updated_at` |
| `iam_organization_staff_membership` / P | `is_manager, enabled, lock_version, updated_at` |
| `iam_staff_permission_grant` / O | `revoked_at` |
| `iam_staff_miniapp_binding` / O | `status, revoked_at, revocation_reason, lock_version` |
| `iam_organization_user` / P | `phone_e164, phone_bound_at, nickname, avatar_url, status, auth_version, lock_version, frozen_at, updated_at`；V9 继续阻断注册身份和来源变化 |
| `iam_organization_user_capability` / P | `enabled, revoked_at, lock_version, updated_at` |
| `iam_platform_login_session` / O | `revoked_at, revocation_reason` |
| `iam_staff_login_session` / O | `revoked_at, revocation_reason` |
| `iam_organization_user_session` / O | `revoked_at, revocation_reason` |
| `dev_device_asset` / P | `lifecycle_status, retired_at, retirement_reason, lock_version, updated_at` |
| `dev_device_deployment` / P | `lifecycle_status, business_enabled, enabled_at, ended_at, end_method, end_reason, lock_version, updated_at` |
| `dev_config_application` / P | `status, reported_version_no, reported_content_sha256, reported_mcu_payload_sha256, edge_persisted_at, mcu_synced_at, applied_at, last_failure_at, last_failure_code, lock_version, updated_at` |
| `dev_deployment_runtime_state` / P | `edge_connection_status, mcu_link_status, safety_status, aggregate_weight_health, camera_health, local_storage_health, clock_sync_health, edge_boot_id, edge_software_version, mcu_firmware_version, mcu_boot_id, uart_state, uart_protocol_major, uart_protocol_minor, capability_bitmap_hex, last_mcu_reset_reason, applied_config_version_no, applied_config_content_sha256, applied_mcu_payload_sha256, local_storage_state, clock_state, pending_reliable_event_count, last_heartbeat_at, last_device_event_at, lock_version, updated_at` |
| `dev_port_runtime_state` / P | `delivery_door_state, delivery_door_actuator_health, delivery_door_contact_state, clean_lock_power_state, clean_solenoid_health, clean_door_inferred_state, clean_door_state_basis, weight_sensor_health, infrared_value, infrared_sensor_health, smoke_state, smoke_sensor_health, safety_status, pending_delivery_result_session_id, last_observed_at, lock_version, updated_at` |
| `dev_device_fault_event` / O | `status, last_detected_at, discovery_count, recovery_source_kind, recovery_source_edge_event_id, recovery_source_edge_event_type, recovery_method, recovery_audit_log_id, recovered_at, recovered_by_staff_account_id, recovery_reason, lock_version` |
| `dev_delivery_session` / P | `status, first_edge_accepted_at, first_physical_progress_at, device_completed_at, ended_at, end_reason, lock_version, updated_at` |
| `dev_device_command` / P | `physical_state, edge_accepted_at, physical_started_at, physical_ended_at, lock_version, updated_at` |

## 7. 自动验证

运行：

```powershell
.\tools\database\verify-f04-migrations.ps1
```

脚本固定验证本机官方 `mysql:8.4` 镜像 ID，创建唯一命名的一次性容器和两个独立空库，
在 `finally` 中清理容器。验证内容包括：

- 四个脚本、准确 30 张表、禁止项与无 seed；
- 两个空库依次严格安装 V1～V4；
- 表清单、78 个外键、84 个唯一约束、166 个 CHECK、79 个普通索引；
- 静态解析四个脚本，证明 78 个外键子列都由显式 DDL 索引左前缀覆盖；
- 逐表证明 21 张机构级业务表都直接引用机构根；
- 两库 `mysqldump --no-data` 结构指纹完全一致；
- 同租户两机构之间的工作人员小程序绑定、注册来源部署和投口运行投影串联全部失败；
- 非规范租户码、越界投口号、非法 UUIDv4/E.164、空白硬件 SN、未激活即启用小程序全部失败；
- 配置失败后沿同一 application 重同步至 `PENDING → APPLIED`，历史最近失败证据仍保留；
- F-10 版本、设备/投口 u32、称重 i32、单价万分值和 32 字符展示名边界值可落库，所有 `max+1/min-1`、首尾空白展示名及非法命令错误码均被拒绝；
- 两个投口可同时保存不同清运锁状态；旧 `MOVING` 门态和残缺整机快照被拒绝；
- 首次称重稳定、最终称重失败的完整两份 measurement 可落库，净重为 `NULL`；
- 错配置摘要、物理成功缺时间、measurement 缺来源字段、投递缺最终关门态和失败测量补写稳定重量均被拒绝；
- 向非空/半成品目标重新执行 V1 必须失败，不能把它当作可继续目标库。

2026-07-24 本地证据：

| 项目 | 结果 |
|---|---|
| MySQL | `8.4.10` |
| 镜像 ID | `sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6` |
| 对象计数 | `30 tables / 78 FK / 84 UQ / 166 CHECK / 79 non-unique indexes` |
| 两库结构 SHA-256 | `cef82b4a9772819a1fb01e37bec3b88cd4cecc7ec9231ec55815a03ee42568f7` |
| 负例 | 建立基础数据前 10 项；建立完整作业后 16 项 |
| 结果 | 两库相同，全部自动检查通过 |

## 8. F-10 联调收口复核项

当前 F-10 机器契约明确要求 `firstPreOpenMeasurement=measurementStable`，只允许最终关门
称重以 `TERMINAL_WEIGHT_FAILURE` 进入失败状态。因此 V4 按当前机器契约约束为：

- 首次开门前测量必须 `STABLE` 且有重量，并保存该次完整 measurement/MCU 来源；
- 最终关门后测量稳定时有重量和净重，明确失败时两者为空；它保存自己的独立 MCU 来源；
- 仍创建 `dev_physical_result`，供后续建立系统异常订单。

F-10 软件契约已经冻结；MCU、真实 Python 3.11 和跨端联调尚未收口。如果联调证明首次
稳定测量可能在最终载荷中永久丢失，应先统一修订 F-10 Schema、业务基线和数据库约束，
再以前向迁移处理，不能仅在数据库放宽为空。

配置表同时保存中心完整版本和当前 F-10 可编码子集。设备展示名、地址、坐标属于中心
元数据；心跳/漏报、关门重试、投递沉降等待和投口门自动关闭等设备执行字段当前没有
F-10 payload 属性。验证脚本只证明这些字段可版本化存储，不能冒充其已完成下发或 MCU
生效；该差额继续作为 F-10 与 MCU/联调收口项。
