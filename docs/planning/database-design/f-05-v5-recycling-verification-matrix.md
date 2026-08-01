# F-05｜目标数据库 V5 recycling 验证矩阵

> 范围：`V5__recycling.sql`，前置为已经完成的 V1～V4。
>
> 事实来源：D-016～D-020、D-031～D-035、D-041～D-045 和 F-05。
>
> 结论：V5 准确创建 **24 张 recycling 表**；V1～V5 合计 54 张表。
> 本迁移不包含 seed、旧数据迁移、Java 状态机或 V6～V10 内容。
>
> **2026-08-01 兼容说明**：本文件验证的是已经执行且不可修改的 V5 历史 DDL，不再作为清运业务规则来源。V5 中清运配置的 `review_mode`、清运记录审核投影和 `rec_clean_revision` 均已废止，应用不得使用；V19 已用于投递配置管理，清运结构由 V20 前向迁移以直接修改字段和 `rec_clean_record_change` 替换。替换前后分别以本矩阵和新的 V20 验证矩阵证明实际结构，不能改写本文件来假装 V5 从未创建过这些对象。

## 1. 约定

- 所有机构级表都直接以 `tenant_id + organization_id` 引用机构根；
- 所有外键子列都由显式 DDL 索引的左前缀覆盖；
- `A` 表示只追加事实，`S` 表示当前槽位，`O` 表示单向收敛，
  `P` 表示受保护当前投影；
- MySQL 不支持延迟外键，父聚合的当前指针均允许合法空中间态，再在同一事务插入子事实并回填；
- 表内 `CHECK` 只证明本行形状；状态迁移、四槽“至少存在”、公式和外部真实性仍由应用事务及验收证明。

## 2. 24 张表

| 表 / 写类 | PK / 稳定身份 | 作用域与强关系 | UQ / CHECK / 首批索引 |
|---|---|---|---|
| `rec_organization_delivery_config` / A | `id`；机构版本 | 机构、发布工作人员 | 机构+版本；M0 `ALL_MANUAL`、负余额阈值 `<0`、认定上限 `1..1000000g`；机构版本倒序 |
| `rec_organization_delivery_config_head` / P | 机构 PK | 当前配置必须属于同机构且版本相同 | 每机构一行；版本/锁版本、切换时间 |
| `rec_organization_order_counter` / P | 机构 PK | 机构根 | 非负提交可见序号和锁版本 |
| `rec_delivery_order` / P | `id`；订单号 | 同机构 session、物理结果、用户、设备/投递配置和袋；session 冻结用户、配置摘要、价格、阈值与袋必须逐项相同 | 订单号、机构可见序号、session、物理结果唯一；负克值允许；当前修订复合回指；审核、用户、部署/投口和袋历史索引 |
| `rec_delivery_anomaly` / A | `id` | 同订单及其来源结果 | 订单+异常码；用户异常只允许 `NEGATIVE_WEIGHT_ANOMALY`；机构异常时间 |
| `rec_delivery_revision` / A | `id`；修订 UUIDv4 | 同订单复合自链；平台管理员/工作人员分型 | 订单+版本、上一版最多一个后继；初审/纠错链、金额差；订单当前指针回指链尾结果 |
| `rec_delivery_photo` / O | `id`；可空照片 UUIDv4 | 同订单 | 订单+四个标准槽、照片 UUID；pending/available/missing 字段组、HTTPS 无查询凭证；机构待补照片 |
| `rec_organization_clean_config` / A | `id`；机构版本 | 机构、发布工作人员 | 机构+版本；V5 历史兼容 `ALL_MANUAL` 和固定 1800 秒；审核字段已废止，由 V20 删除；机构版本倒序 |
| `rec_organization_clean_config_head` / P | 机构 PK | 当前配置必须属于同机构且版本相同 | 每机构一行；版本/锁版本、切换时间 |
| `rec_organization_clean_record_counter` / P | 机构 PK | 机构根 | 非负提交可见序号和锁版本 |
| `rec_clean_operation` / P | `id`；操作 UUIDv4 | 同部署投口、清运员、配置、新旧袋、可空旧基准/待处理 session；完成记录强回指 | 活动投口生成槽；旧袋缺失、旧基准、开锁前称重、不可逆解锁/断电/人工确认和终态形状；状态期限、清运员历史 |
| `rec_clean_record` / P | `id`；清运记录号 | 同操作及目标为该操作的物理结果、清运员、配置和新旧袋 | 记录号、机构可见序号、操作、结果唯一；原始重量/可靠性；V5 审核投影与待审核索引已废止且由 V20 删除 |
| `rec_clean_anomaly` / A | `id` | 同清运记录 | 记录+异常码；机构异常时间 |
| `rec_clean_revision` / A | `id`；修订 UUIDv4 | V5 历史清运审核结构 | 已废止，应用不得读写，由 V20 连同记录回指一起删除 |
| `rec_clean_photo` / O | `id`；可空照片 UUIDv4 | 同清运操作 | 操作+首次开门/最终关门四槽；状态、URL/摘要/大小/缺失字段组；机构待补照片 |
| `rec_bag` / A | `id`；全局袋码 | 固定机构归属 | 全局大小写敏感 URL-safe 袋码、机构候选键；**无生命周期状态列** |
| `rec_bag_current_occupancy` / S | 袋 PK | 投口绑定或与操作新袋相同的清运预留二选一 | 投口、清运操作各唯一；目标 XOR |
| `rec_bag_occupancy_event` / A | `id`；事件 UUIDv4 | 同袋、投口、可空清运操作 | 清运操作+事件类型；来源形状；袋/投口时间线 |
| `rec_port_weight_baseline` / A | `id`；投口版本 | 同投口/袋；分型袋事件、物理结果、清运记录或重测 | 投口+版本及各来源唯一；有效基准 `>=0`、来源 XOR；投口版本倒序 |
| `rec_port_baseline_measurement` / P | `id`；测量 UUIDv4 | 同部署投口/袋/设备及投口配置；目标为本测量的结果；形成基准强回指 | 活动投口、物理结果唯一；pending/completed/failed/stale 字段组；待执行、投口历史 |
| `rec_port_capacity_state` / P | 投口 PK | 同部署投口；当前基准、检测和满溢事件必须属于同投口 | 基准/gate/满溢投影；原始净重允许负，展示百分比可超过 100%；机构容量阻断 |
| `rec_fullness_detection` / P | `id`；检测 UUIDv4 | 同投口/袋/设备及投口配置；订单/清运记录/人工来源三选一；基准 snapshot 同袋同投口；样本强回指 | 每订单/清运记录一次、活动投口槽；来源、基准和终态形状；领取与投口历史 |
| `rec_fullness_sample` / A | `id` | 同检测；物理结果必须以本 sample 为目标 | 检测+角色、物理结果唯一；红外/重量来源、负原始净重、百分比和综合结论字段组 |
| `rec_fullness_event` / O | `id`；事件 UUIDv4 | 同投口的首次、确认、最近、最近满溢和恢复检测 | 活动投口槽；active/recovered 形状；机构活动满溢和首次确认时间 |

## 2.1 H-02 运行账号列级更新矩阵

下表补齐 P/O 写类的精确运行列。未列出的作用域、稳定身份、设备/配置/袋/重量来源
快照、首次证据和创建时间不可更新；A 表没有 `UPDATE/DELETE`，当前袋槽只允许
`SELECT/INSERT/DELETE`。

| 表 / 写类 | `ecobin_app` 可更新列 |
|---|---|
| `rec_organization_delivery_config_head` / P | `current_config_id, current_version_no, lock_version, switched_at, updated_at` |
| `rec_organization_order_counter` / P | `last_visibility_sequence_no, lock_version, updated_at` |
| `rec_delivery_order` / P | `review_status, current_revision_no, current_revision_id, final_business_weight_kg, final_amount_cent, first_approved_at, updated_at` |
| `rec_delivery_photo` / O | `photo_uid, status, object_url, sha256, size_bytes, captured_at, linked_at, missing_reason, updated_at` |
| `rec_organization_clean_config_head` / P | `current_config_id, current_version_no, lock_version, switched_at, updated_at` |
| `rec_organization_clean_record_counter` / P | `last_visibility_sequence_no, lock_version, updated_at` |
| `rec_clean_operation` / P | `pre_unlock_weight_status, pre_unlock_weight_g, pre_unlock_weight_fault_code, status, edge_saved_at, first_possible_unlock_at, solenoid_powered_off_at, cleaner_confirmed_closed_at, pre_unlock_end_requested_at, recovery_requested_at, reopen_count, recovery_count, completion_record_id, ended_at, end_reason, lock_version, updated_at` |
| `rec_clean_record` / P | V5 曾允许更新 `recalculated_removed_net_weight_status, recalculated_removed_net_weight_g, review_status, review_revision_no, review_revision_id, final_recognized_net_weight_kg, updated_at`；2026-08-01 后应用不得再执行清运审核更新。审核相关列由 V20 删除并新增当前有效重量/来源、备注、修改版本和更新时间；目标写类保持 P，但原始设备/复算快照仍不可覆盖 |
| `rec_clean_photo` / O | `photo_uid, status, object_url, sha256, size_bytes, captured_at, linked_at, missing_reason, updated_at` |
| `rec_port_baseline_measurement` / P | `status, physical_result_id, stable_total_weight_g, fault_code, result_baseline_id, started_at, completed_at, lock_version, updated_at` |
| `rec_port_capacity_state` / P | `baseline_state, current_baseline_id, current_baseline_weight_g, latest_stable_total_weight_g, raw_net_weight_g, displayed_fullness_percent, detection_gate, current_detection_id, current_rule_fingerprint, confirmed_fullness_state, last_detection_id, current_fullness_event_id, lock_version, updated_at` |
| `rec_fullness_detection` / P | `status, final_result, failure_code, disposition, initial_sample_id, initial_sample_conclusion, terminal_sample_id, terminal_sample_conclusion, next_sample_at, completed_at, lock_version, updated_at` |
| `rec_fullness_event` / O | `status, confirmed_detection_id, confirmed_at, current_reason, latest_detection_id, latest_full_detection_id, detection_count, recovered_by_detection_id, recovered_at, updated_at` |

## 3. V5 对 V1～V4 的候选键补强

V5 不修改已经发布的 V1～V4 文件，而是在本迁移内以前向 `ALTER TABLE` 增加：

1. `dev_delivery_session` 的订单冻结快照候选键，使订单不能换用户、配置摘要、价格、阈值或袋；
2. `dev_port_config_snapshot` 的 recycling 复合候选键，使基准重测/满溢检测不能引用同机构另一投口的 snapshot；
3. `dev_physical_result` 的清运、满溢 sample、基准重测目标候选键，使 recycling 子事实能证明结果目标一致。

仍由 V8 闭合的反向跨模块关系包括：

1. `dev_delivery_session → rec_organization_delivery_config / rec_bag`；
2. `dev_device_occupancy → rec_clean_operation`；
3. `dev_device_command → rec_clean_operation / rec_fullness_detection / rec_port_baseline_measurement`；
4. `dev_physical_result` 的三个目标列反向引用 V5 对应聚合。

V5 已建立 recycling 侧指向 device 结果的强复合外键；上述 V8 项不能被裸 ID 外键替代。

## 4. 自动验证

运行：

```powershell
.\tools\database\verify-f05-migrations.ps1
```

验证器固定官方 `mysql:8.4` 镜像 ID，创建两个独立空库并依次严格执行 V1～V5，
在 `finally` 中删除临时容器。覆盖：

- V5 准确 24 张表，V1～V5 合计 54 张表，无 seed、`IF NOT EXISTS` 或关闭外键；
- 两套空库结构完全一致，24 张 recycling 表清单一致；
- 全 schema 189 个外键全部由显式索引左前缀覆盖；
- 24 张 recycling 表全部直接引用机构根；
- 跨机构 config head、非法投递审核模式/历史清运审核兼容值/阈值/袋码/槽位、残缺照片、负有效基准、
  缺关门证据的完成操作、混合检测来源和无基准伪造百分比均被拒绝；
- 大小写不同的袋码可分别保存，完全相同袋码仍全局冲突；
- session 整场净重为负但最终锁存标志为 `false` 时，订单原样保存 `false`，不补判；
- 一单四个标准投递照片槽、旧袋缺失清运准备和新袋预留均可落库；
- 容量原始净重允许负且展示为 0%，可靠满溢度允许超过 100%；
- 已审核订单的当前修订强指针和负认定重量/金额可落库；
- 向非空库重复执行 V5 会失败。

2026-07-25 本地证据：

| 项目 | 结果 |
|---|---|
| MySQL | `8.4.10` |
| 镜像 ID | `sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6` |
| 对象计数 | `54 tables / 189 FK / 180 UQ / 258 CHECK / 186 non-unique indexes` |
| recycling 直接机构 FK | `24 / 24` |
| 两库结构 SHA-256 | `af3684aca14e082afa624a425f2d7aeca675b39dfd8300e66eb5b3f744c40ca7` |
| 数据正反例 | `16` 个负例；6 类关键正例 |
| Java 回归 | JDK `21.0.10` 下执行完整 `.\mvnw.cmd test`，退出码为 `0` |
| 结果 | 两库结构相同，全部自动检查通过 |
