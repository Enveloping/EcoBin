# EcoBin P0 目标数据库设计：约束、索引与写权限（D-031～D-035）

> 总索引：[database-design-draft.md](../database-design-draft.md)
>
> 状态：**D-031～D-035 已确认**
>
> 说明：本文件是数据库设计草案的分章正文，与其余章节共同组成一份设计；决策编号与原正文保持不变。

## 11. 第七批已确认：外键、唯一键、CHECK、索引与写权限

本批不改变 D-001～D-030 的业务含义，而是把全部目标表收束为可由 MySQL 8.0.16+ 实际执行的约束策略。数据库负责证明“本行形状正确、引用确实存在且作用域相同、同一稳定身份最多一份”；跨多行数量、父对象当前状态、严格版本连续、合法状态迁移和多聚合原子结果仍由下一批事务/锁序设计负责，不能写成实际上不起作用的伪约束。

### D-031 作用域候选键、强类型外键与建表顺序

**已确认：所有可强类型引用的业务关系都建立真实外键，并把租户/机构作用域一起放进候选键；只有 operations 中明确列出的诊断导航允许使用受控通用引用。**

#### 统一候选键和外键规则

| 父对象级别 | 父表必须提供 | 子表引用方式 |
|---|---|---|
| 平台级 | `PRIMARY KEY(id)` 及已确认的稳定业务唯一键 | 使用强类型单列 ID 外键；不得附会租户成员关系 |
| 租户根 | `iam_tenant.id` 主键 | `tenant_id -> iam_tenant.id` |
| 租户级父表 | `UNIQUE(tenant_id, id)` | `FOREIGN KEY(tenant_id, parent_id)`，验证父对象确属该租户 |
| 机构根 | `iam_organization` 提供 `UNIQUE(tenant_id, id)` | `FOREIGN KEY(tenant_id, organization_id)` |
| 机构级父表 | `UNIQUE(tenant_id, organization_id, id)` | `FOREIGN KEY(tenant_id, organization_id, parent_id)`，不得只引用裸 `id` |
| operations 可变作用域父表 | 依次提供 `(scope_kind,id)`、`(scope_kind,tenant_id,id)`、`(scope_kind,tenant_id,organization_id,id)` 候选键 | 子表建立同层级 FK；平台/未解析行由第一层校验，租户行再由第二层校验，机构行再由第三层校验 |

- 所有业务外键显式使用 `ON DELETE RESTRICT ON UPDATE RESTRICT`，不使用级联删除或级联改写历史归属；外键子列和被引用候选键的类型、字符集、排序规则、顺序完全一致。
- 被引用候选键只包含创建后不可变的身份、作用域、目标类型或结果快照；不得把以后会推进的状态、次数、最近时间等可变投影放进候选键，否则 `ON UPDATE RESTRICT` 会反过来阻断合法收敛。需要验证“当前是否 ACTIVE/PAUSED”等条件时由锁内事务复核，而不是把可变状态塞进外键。
- MySQL 的复合外键只要任一子列为 `NULL` 就不会检查该行，因此任何包含可空作用域或可空分型引用的关系都不能只建一条“大而全”复合 FK。它必须同时有不含可空作用域的强类型身份 FK，并按上表逐层追加作用域 FK；`CHECK` 再保证何种作用域下哪些列必须为空或非空。
- 每张机构级业务表直接引用机构根；它引用其他机构级父对象时再建立带作用域的复合外键。这样即使应用漏写机构过滤，也不能把 A 机构订单连接到 B 机构用户、设备、袋、钱包或渠道单。
- `tenant_id`、`organization_id`、历史父 ID 和作用域快照创建后不可修改。设备调拨仍通过结束旧部署、新增部署完成，不更新历史机构。
- 平台管理员/工作人员/系统等操作者使用分型可空强外键和本行 `CHECK`，不使用一个裸 `actor_type + actor_id`。机构工作人员外键同时带租户；需要机构权限的事实还带机构并引用其任职关系。外键只能证明任职记录存在，当前是否启用、是否仍有权限必须在事务中复核。
- `ops_audit_log.target`、`ops_reliable_task.target`、`ops_alert.source`、`ops_reconciliation_issue.subject` 和 `ops_reconciliation_action.result` 允许使用白名单 `type + stable_key`，因为它们只负责导航、执行或诊断，不是授权和账务真相。其作用域必须在创建时从权威对象复制并校验，执行器按固定类型注册表解析；禁止通用反射式跨表更新。
- `ops_task_attempt` 复制任务的不可变 `scope_kind + tenant_id + organization_id`，并通过上述三层 FK 逐级引用任务；资金渠道观察再用机构层复合外键证明“这次尝试”和渠道单属于同一机构，同时保留直接 attempt 身份 FK，避免任何可空列让存在性检查失效。

#### 必须补强的强类型关系

1. `dev_device_command` 为投递 session 开始、清运、满溢、空袋基准重测和配置五类目标保留互斥的强类型外键；`dev_physical_result` 为投递 session、清运、满溢和基准重测结果保留互斥强类型外键。会话内本地继续开关门不建立命令目标、命令事件或物理结果。`dev_edge_event` 以唯一来源 inbox 和部署内全局序号约束跨类型事件身份；命令事件、物理结果分别以唯一 `edge_event_id` 一对一引用公共头，不能各自再拥有独立事件 UUID/序号。父表提供包含“作用域 + 本行 ID + 对应目标 ID”的候选键。recycling 子表引用物理结果时同时携带自己的目标 ID，防止关联到同机构的另一场作业结果。
2. `dev_delivery_session` 是投递结果的唯一父根：`dev_physical_result.delivery_session_id` 提供唯一键并以“作用域 + 部署 + 投口 + session”复合外键回指 session；`rec_delivery_order` 同时强引用同一 session 和目标为该 session 的物理结果，并分别唯一。这里没有周期子表、会话内序号或继续 pending 循环，因而不需要为本地轮次构造可空中间指针。session 的 `BUSINESS_CONFIRMED` 只能在唯一物理结果、唯一订单和最终满溢 gate 同事务成立后推进。
3. `rec_delivery_order.current_revision_id`、`rec_clean_operation.completion_record_id`、`rec_clean_record.review_revision_id`、`rec_port_baseline_measurement.result_baseline_id`、`rec_fullness_detection.initial_sample_id/terminal_sample_id`、`rec_port_capacity_state` 的当前基准/检测/事件指针以及 `fund_payout_gate.current_pause_event_id` 均使用“父聚合 ID + 子 ID + 必要结果字段”的复合外键，不能只引用一个合法但属于其他聚合的子行。`DELIVERY_COMPLETE` 检测强引用唯一投递订单，订单再唯一引用 session；检测样本指针只在该终态需要设备采样时非空。创建时即可从权威无效新基准确定的 `CLEAN_COMPLETE + WEIGHT_BASELINE_UNAVAILABLE` 失败 gate 按 D-020 允许两个样本指针都为空，并由触发类型、基准状态、失败码和终态组合 CHECK 把例外收窄。清运 M0 只有一次初审，因此使用 `review_revision_id` 而不是暗示可多次纠错的 `current_revision_id`；审核后纠错仍只属于投递订单。
4. `rec_delivery_revision.previous_revision_id` 使用包含订单、上一版本号及上一版结果的复合自外键；这样本版 `before` 值确实来自同一订单的上一版 `after` 值。订单当前指针再复合引用链尾的版本号、最终重量和金额。
5. `iam_organization_user.registered_via_deployment_id` 保持可空，但非空时复合引用注册时同机构部署；该值和 `registered_at` 一经插入永远不可回填或修改。

MySQL 没有延迟外键。上述所有“父行当前指针 → 子事实”的关系都必须允许合法构造中间态：父对象在尚未到达对应完成/审核/终态时指针为 `NULL`，事务按“插入父或锁定既有父 → 插入子事实 → 回填指针并推进状态”执行；`CHECK` 只在完成状态要求指针非空。不能要求父行首次插入就引用尚不存在的子行。

#### DDL 依赖顺序

建表按“IAM 租户/机构/工作人员/小程序基础 → 设备资产、部署、投口、配置 → 机构用户 → 设备作业 → recycling → funds → operations”推进；双向当前指针和跨模块引用在两端表都存在后以 `ALTER TABLE` 添加。Flyway 不通过关闭 `FOREIGN_KEY_CHECKS` 掩盖错误顺序，空库安装和升级测试都保持外键检查开启。

### D-032 稳定身份唯一键、当前槽位与强指针

**已确认：永久身份使用普通唯一键；“历史可多条、当前最多一条”统一使用独立槽位表或终态返回 `NULL` 的生成列，不使用 `UNIQUE(..., nullable_column)` 假装当前唯一。**

MySQL 唯一索引允许多个 `NULL`。因此手机号可空唯一中的多个未绑手机号是正确行为，但授权、绑定和活动事件若直接把可空机构/结束时间放入唯一键会失效。统一采用以下两种机制：

- 需要跨聚合互斥、事务中会转移的占位使用独立当前表：`dev_asset_active_deployment`、`dev_device_occupancy`、`rec_bag_current_occupancy`、`fund_active_withdrawal`。这些表只物理插入/删除，不保留软删除历史；历史由部署、作业、袋事件和提现单保存。
- 同一历史表中需要唯一活动行时，增加 `STORED` 生成列，例如 `CASE WHEN status='ACTIVE' THEN stable_key ELSE NULL END` 并建立唯一键；终态行生成 `NULL`，所以历史可以保留多次。

#### 各表族唯一键收口

| 表族 | 必须落地的唯一身份和当前槽位 |
|---|---|
| identity | 平台管理员、工作人员账号、机构用户和工作人员小程序绑定分别以公开 UUIDv4 唯一；平台登录名在独立平台命名空间唯一，工作人员登录名在所有租户间全平台唯一；租户码唯一；机构码在租户内唯一；AppID 全平台唯一且每机构一个；租户主体账号用 `principal_tenant_slot` 保证每租户最多一个且禁用后仍占槽；任职按机构+员工唯一；权限定义按代码+作用域唯一；有效授权用“非空规范作用域判别键 + active marker”唯一；用户按 AppID+OpenID、机构+非空手机号唯一；用户 capability 唯一；管理绑定分别保证同机构用户最多一个当前员工、同员工+AppID 最多一个当前用户；三类会话各自以 UUIDv4 `session_uid` 唯一。 |
| device | 硬件 SN、部署公开码唯一；OneNet 产品来自系统配置且设备名由 SN 推导，不建立冗余身份唯一键；资产当前部署以槽位表唯一；投口按部署+编号唯一；配置按部署+版本唯一，投口快照按配置+投口唯一，应用过程按配置唯一；运行投影与部署/投口一对一；活动故障键唯一；整机占位按资产唯一；投递 session UUID 唯一；命令 UUID 唯一；公共边缘事件 UUID、来源 inbox 全局唯一且 `(deployment_id, edge_event_sequence)` 唯一；命令事件/物理结果分别与公共事件头一对一；每个 session、清运操作或满溢样本最多一个对应物理结果。 |
| recycling | 投递/清运配置按机构+版本唯一；订单号、投递 session、物理结果一对一；异常按单据+异常码唯一；投递修订按订单+版本唯一且一版最多一个后继；照片按单据或操作+固定位置唯一；清运操作 UUID、清运记录号、操作和物理结果一对一；未结束清运按投口活动唯一；袋码全平台唯一，当前袋与投口/清运预留双向唯一；袋事件 UUID 及清运来源+事件类型唯一；基准按投口+版本和来源唯一；基准重测 UUID、物理结果和未结束投口各自唯一；检测 UUID、投递订单/清运来源唯一，未结束检测按投口唯一；样本按检测+角色且物理结果唯一；活动满溢事件按投口唯一。 |
| funds | 提现配置按机构+版本唯一且当前 head 与机构一对一；钱包按机构用户一对一；机构出款账户按机构一对一；两类明细 UUID 唯一，投递修订/充值/调整最多入账一次，提现按 `FREEZE/FINAL` 各最多一次；充值号、提现号、支付/转账外部单号唯一；充值与支付、提现与审核、提现与转账一对一；活动提现以钱包为主键且提现单唯一；小程序与系统商户绑定唯一；闸门与商户一对一，暂停触发观察和恢复所指暂停事件分别唯一。 |
| operations | inbox 按来源主体+外部消息 ID 唯一，隔离按规范去重键唯一；可靠任务 `task_uid/task_key` 唯一且一个 inbox/设备命令最多一个任务；尝试按任务+序号及 lease token 唯一；审计 `request_uid` 唯一，成功操作生成槽唯一且 `SUCCEEDED` 必须有 `operation_uid`；告警来源永久唯一、活动聚合键唯一；每日对账 run 按商户+业务日唯一；对账 issue 只对未解决 `active_dedupe_key` 唯一，解决后复发新建 issue；action UUID 和幂等 action key 唯一。 |

授权的 `scope_organization_key` 不依赖自增 ID 的 0 哨兵，而使用 ASCII 二进制规范判别值：租户级为固定 `T`，机构级为无歧义的 `O:<organization_id>`。所有生成键的算法、字符集和规范输入在迁移中固定，并用真实 MySQL 验证并发唯一冲突；唯一或摘要冲突后重读原行并比较完整规范输入或权威业务字段，不能只再次比较同一个摘要，更不能盲目当成功。

这些唯一键只证明“最多一次/最多一条”。“订单一定有四个照片槽”“启用租户一定有主体账号”“非终态作业一定有占位”“提现两侧明细一定同时存在”“终态检测一定具备正确数量的样本”等至少一次和跨表计数事实，必须由创建/完成事务及一致性巡检保证。

### D-033 本行 CHECK、状态形状与数据库能力边界

**已确认：能由单行确定的非法数据全部用命名 CHECK 拒绝；状态迁移、跨表公式和外部真实性明确留给条件更新、事务及验收，不新增通用触发器伪装业务状态机。**

#### 必须建立的 CHECK 类别

| 类别 | 约束内容 |
|---|---|
| 作用域与分型引用 | operations 先校验四类作用域的租户/机构空值组合，再按表收窄允许值：inbox/task 不允许 `UNRESOLVED`，资金事实固定机构级、run 固定平台级，只有隔离及已确认的安全/告警/对账诊断允许未解析；平台管理员/工作人员/系统操作者恰好一种；投递结果/订单必须具有同一机构用户和 session，不允许脱离 session 的无主恢复；命令、物理结果、检测来源、资金明细来源、渠道观察来源恰好一种。 |
| 内部状态与时间 | 内部稳定枚举使用 ASCII 二进制/大小写敏感列上的 `VARCHAR + CHECK`，避免 `_ci` 排序规则让 `active` 冒充 `ACTIVE`；终态必须有终结时间，非终态不得伪造终态字段；撤销/恢复/审核/发布时间不得早于创建时间；一组租约、结果、确认或失败字段必须全空或成组非空。微信原始渠道状态和外部错误码保持可扩展字符串，不用封闭 CHECK 把未知新状态误判为失败。 |
| 金额和重量 | 分、克使用 `BIGINT`；用户可用余额允许负数，所有冻结和机构余额非负；满溢原始净重允许负、展示百分比为空或 `>=0` 且允许超过 100%；单价 `>0`；负重量异常阈值必须为正整数克且默认 500；可靠重量必须有值，缺失不能用 0 冒充。最终布尔异常标志只复制边缘按本地轮次锁存的最终载荷值；后端不得按整场首末净重补判，也不保存本地触发轮次及其减少值。 |
| 资金代数 | 每条明细都满足 `before + delta = after`，以足够宽的 `DECIMAL` 中间表达式避免溢出；充值满足毛额=手续费+净额，毛额 100～20000000 分，手续费按固定 6000 ppm 向上取整；提现金额及固化配置满足 `10 <= min <= amount <= max <= hard_limit <= 20000`。 |
| M0 范围 | 投递、清运审核模式固定 `ALL_MANUAL`；手动提现免审阈值固定 0，保留字段但由以后前向迁移放宽；自动提现字段不进入 M0。 |
| 历史链与槽位形状 | 投递初审为版本 1 且前值为空；纠错版本号为上一版+1并校验金额差额；公共边缘事件序号严格为正、交付类别与事件注册表一致、设备时间与时钟质量成组；投递照片只有首次开门前/整场最终关门后四槽且状态与 `photo_uid/URL/摘要/大小/缺失` 字段匹配；袋占位目标恰好一种；清运旧袋/旧基准状态与可空列匹配，`COMPLETED` 必须同时有电磁阀断电和人工确认，`PRE_UNLOCK_ENDED` 不得存在可能通电时间；活动/恢复事件与恢复字段匹配；可靠任务终态要求 `handled_wake_version=wake_version`。 |

每个 CHECK 使用全 schema 唯一、以表名开头的名称。MySQL 只在表达式结果为 `FALSE` 时拒绝，结果为 `NULL/UNKNOWN` 会通过；因此必填枚举和代数操作数必须同时声明 `NOT NULL`，可空字段必须显式写成 `x IS NULL OR ...`，XOR/成组字段则逐项使用 `IS NULL/IS NOT NULL`，不能只写一个会被 `NULL` 绕过的 `IN` 或等式。资金等式先分别把每个操作数转换为足够宽的 `DECIMAL`，再执行加减，避免在转换前先发生 `BIGINT` 溢出。

MySQL 8.0.16 继续作为 CHECK 真正执行而非只解析的能力来源说明；但 D-041 已将本项目唯一发布和验收目标收口为锁定完整版本及 digest 的 MySQL 8.4 LTS，因此不再承诺 8.0.16 部署兼容性，原定的真实 8.0.16 负向迁移测试也统一改在目标 8.4 执行。目标 DDL 仍不使用参与 CHECK 的表达式默认值，所有默认值采用字面量或由应用显式写入，并必须通过“故意插入非法值应失败”的真实 MySQL 负测；禁止以 H2 通过代替。

#### 明确不能由普通 CHECK 证明的内容

- `OLD -> NEW` 是否是合法状态迁移、终态是否从不回退、字段是否只允许从空补写一次；这类更新必须包含预期旧状态/版本/空值条件并检查影响行数。
- 父租户、机构、账号、任职、授权、绑定、设备或商户当前是否启用；外键只证明其存在。
- 版本号和边缘事件序号是否严格连续、配置是否恰好覆盖全部投口、照片或样本是否达到所需数量。
- 钱包/机构账户投影是否等于最后一条明细、提现两侧是否同时冻结/结算、订单修订差额是否已经同步进入钱包、对账自动修复前置状态是否仍完整。
- 微信、OneNet、MCU、传感器或 COS 的外部事实是否真实；数据库只能约束本地保存的可信观察和摘要。

一个 `ops_task_attempt` 只允许代表一次明确的处理或外部调用动作；`SUBMIT/QUERY/CLOSE/CANCEL` 必须是不同 attempt。渠道观察对 `source_task_attempt_id` 唯一，跨支付观察和转账观察的全局互斥由任务类型白名单、私有写入口和验收测试保证，不能让同一 attempt 同时解释两次网络调用。

### D-034 查询驱动索引与约束索引

**已确认：先为外键、唯一身份、当前槽位和已经冻结的查询/领取路径建索引；不为每个状态、布尔或 JSON 字段机械建单列索引。**

- 每个外键在子表显式创建名称稳定、列序完全匹配的左前缀索引，即使 InnoDB 可以自动创建，也不依赖隐式索引及其后续静默替换。
- 机构后台列表统一以 `(tenant_id, organization_id, <高选择性状态/主体>, <业务时间>, <稳定排序键>)` 开头；父聚合时间线统一以 `(parent_id, <领域序号或 occurred_at>, <稳定排序键>)` 开头。分页使用与接口完全相同的稳定游标排序，不用无索引大偏移作为主要方案。公开业务单号或领域序号已经承担排序时，不能在设计中笼统写成 `id` 后让实现改用另一顺序。
- 唯一键已经满足同前缀等值查询时不再建立重复普通索引；单独的 `enabled`、布尔、低基数状态和 JSON 不建索引。原始报文、诊断载荷和展示参数不参与普通搜索。

| 读写路径 | 首批索引 |
|---|---|
| 身份与会话 | 四类公开 UUID、平台与工作人员各自命名空间的登录名、租户码、机构码、AppID、AppID+OpenID、机构+手机号的唯一索引；任职按员工和机构两条访问路径；授权/能力按主体+活动标记；三类会话按主体+撤销+过期及全局过期清理。地推统计使用已确认的 `(tenant_id, organization_id, registered_via_deployment_id, registered_at)`。 |
| 设备与配置 | 机构设备列表按部署生命周期/后台启停；资产部署历史；配置和应用按部署+版本/状态；离线扫描按 edge 状态+最后心跳；端口阻断健康；活动故障按机构+状态+影响+最近时间；session 开始授权/结果恢复超时扫描；命令按部署+物理状态；事件/结果按部署+接收时间和各强类型目标。 |
| 投递与清运 | 机构审核队列至少按 `(tenant_id, organization_id, review_status, device_occurred_at, delivery_order_no)`，用户订单至少按 `(organization_user_id, device_occurred_at, delivery_order_no)`，两者查询都附加机构 `visibility_sequence_no <= snapshot`；为部署/投口筛选补与同一排序兼容的候选索引。机构计数器唯一分配提交可见序号；异常按机构+异常码+时间；照片按机构+待补状态+时间；清运操作按机构+状态+期限和清运员历史；袋/投口事件时间线；基准按投口+版本；容量按机构+检测 gate/满溢状态。 |
| 满溢 | 检测领取按状态+下次采样时间，投口检测历史；活动满溢按机构+状态+首次确认时间；样本按检测+角色唯一即可，不另建重复时间索引。 |
| 资金 | 用户钱包明细唯一 `(wallet_id, entry_sequence_no)` 并按该序号读取；机构钱包流水附加 `visibility_sequence_no <= snapshot`，再按机构+发生时间+钱包/用户+钱包内序号读取，并为用户、类型和来源筛选保留可验证的候选索引；机构账户明细按账户+发生时间；充值按机构+状态+创建时间及全局状态+到期时间；提现按机构+状态+创建时间、用户/钱包时间线、长时间未结算，以及机构+成功终态+渠道终态时间的期间汇总；支付/转账按商户外部单号、当前渠道状态+更新时间；观察按所属渠道单+时间；闸门事件按商户+时间。 |
| operations | inbox 按状态+首次接收时间和作用域+时间；隔离按状态+原因+时间；可靠任务先以 `(state, execution_lane, claimable_at, priority, id)` 作为候选领取索引；attempt 按任务+序号/时间；审计按作用域+时间、动作+结果、操作者；告警按作用域+状态+严重级+最近时间；对账 run 按商户+业务日，issue 按作用域+状态+严重级+时间，action 按 issue/run/task+时间。 |

正式迁移前用目标规模数据和真实查询执行 `EXPLAIN`/可用的 `EXPLAIN ANALYZE` 并记录实际耗时；所有诊断语法以 D-041 锁定的 MySQL 8.4 目标版本验证，不再为 8.0.16 部署兼容性收缩验收。任务领取尤其要先冻结真实的 `WHERE + ORDER BY + FOR UPDATE SKIP LOCKED` SQL：`claimable_at <= now` 是范围条件，其后的 `priority,id` 不当然继续承担有序扫描，所以 D-027 的五列索引只是首个候选，最终列序或是否拆分索引以真实执行计划和并发测试为准。只有有明确查询、选择性和排序用途的候选索引才保留。新增索引还要核对是否与唯一/外键索引互为前缀，避免为了“看起来完整”显著放大账本、观察和设备事件的写成本。

I-040 概览最长只查询 31 个业务日，优先复用注册归因、投递/清运业务时间、提现渠道终态时间及当前投影索引；正式 SQL 必须用目标规模逐项 `EXPLAIN ANALYZE`。M0 不因概览再建跨模块累计表、通用统计 JSON 索引或无查询选择性的状态单列索引；只有现有领域索引无法满足已冻结口径时，才增加该领域自己的复合索引。

### D-035 不可变事实、数据库账号与最小写权限

**已确认：迁移所有者和运行账号分离；运行账号没有 DDL、GRANT 或全库 UPDATE/DELETE，按表和列授予最小 DML。数据库权限负责阻断越权写法，领域事务和私有 Mapper 负责合法状态机。**

#### 数据库账号边界

1. `ecobin_schema_owner` 只在受控 Flyway 阶段使用，拥有目标 schema 的 DDL 和授权能力；应用进程、定时任务和人工后台都不持有该凭证。
2. `ecobin_app` 只获得目标表所需 `SELECT/INSERT`、极少量列级 `UPDATE` 以及当前槽位所需 `DELETE`；不得拥有 `CREATE/ALTER/DROP/TRUNCATE/INDEX/TRIGGER/GRANT/REFERENCES`，也不直接访问旧数据库。
3. MySQL 权限是累加的：对依赖列级保护的表，不能同时给 `ecobin_app` 或其任何激活角色更高层级的全库或整表 `UPDATE`。授权脚本由迁移中的权限矩阵生成，Flyway 使用独立 owner 数据源，并以 `SHOW GRANTS` 连同有效角色做验收。
4. 模块所有权无法在“一个 Spring DataSource + 一个数据库账号”内按 Maven 模块区分，因此仍需私有 repository/Mapper、禁止跨模块直接引用实现、ArchUnit 规则和真实 MySQL 事务测试；不建立可接受任意表名或任意状态的通用更新器。

#### 表级写策略

| 写策略 | 表及允许动作 |
|---|---|
| 目录只读 | `iam_permission_definition` 仅由迁移维护，运行账号只读。 |
| 只追加事实 | 设备静态身份/配置 `dev_port`、`dev_config_version`、`dev_port_config_snapshot`；业务配置 `rec_organization_delivery_config`、`rec_organization_clean_config`、`fund_organization_withdraw_config`；设备事件/结果 `dev_edge_event`、`dev_device_command_event`、`dev_physical_result`；异常/修订 `rec_delivery_anomaly`、`rec_delivery_revision`、`rec_clean_anomaly`、`rec_clean_revision`；袋/基准/采样 `rec_bag`、`rec_bag_occupancy_event`、`rec_port_weight_baseline`、`rec_fullness_sample`；资金事实 `fund_user_wallet_entry`、`fund_wallet_adjustment`、`fund_organization_payout_entry`、`fund_withdrawal_review`、两张微信 observation、`fund_payout_gate_event`；operations 事实 `ops_audit_log`、`ops_reconciliation_action`。这些表运行账号没有 `UPDATE/DELETE`。基准重测是可单调收敛的操作聚合，不列入只追加表；其形成的有效基准仍只追加。 |
| 当前槽位 | `dev_asset_active_deployment`、`dev_device_occupancy`、`rec_bag_current_occupancy`、`fund_active_withdrawal` 仅允许所属领域事务 `SELECT/INSERT/DELETE`；改变归属使用删除旧槽+插入新槽，不在原行改挂。 |
| 一次补齐/单向收敛 | 三张 session 只更新撤销列；两张照片只从 pending 到 available/missing；`ops_task_attempt` 的调用边界、接管和结果各只从空补一次；`iam_staff_permission_grant`、`iam_staff_miniapp_binding` 只允许撤销；故障、满溢事件、告警和对账 issue 只更新重复发现/确认并最终恢复或解决，绝不 reopen。每种写法使用 `WHERE old_state/version/column IS NULL` 并检查影响行数。 |
| 受保护当前投影 | 账号/租户/机构/小程序/任职/用户/能力、资产/部署/配置应用/runtime、delivery session/command、投递订单、清运配置 head/计数器/操作/记录、基准重测、容量/检测、提现配置 head、钱包/机构账户、充值/提现/商户绑定/支付/转账/闸门、inbox/task/reconciliation run 只授予其状态机实际需要更新的列；身份、作用域、金额/重量来源快照、稳定 ID、首次证据和创建时间不在 UPDATE 授权中。 |

AppID 激活时间、激活后的 AppID/作用域，以及机构用户的 `registered_at/registered_via_deployment_id/AppID/OpenID/作用域` 延续 D-013 已确认做法，增加极小且可测试的 `BEFORE UPDATE` 不可变触发器作为纵深防御。触发器使用长期保留、禁止交互登录且仅具执行所需最小表权限的稳定 `DEFINER`，不能依赖部署后会被删除的临时账号；该 definer 禁止作为交互式迁移身份，V9 只能由 `ecobin_schema_owner` 显式指定它创建触发器。实例初始化步骤必须预先创建 definer，并授予 schema owner 在锁定的 MySQL 8.4 版本指定其他 definer 所需的准确动态权限，再用 `SHOW GRANTS` 验证，不能误以为普通 schema DDL 权限已经足够。其余业务状态不建立一套难以维护的通用触发器状态机，而由命名 CHECK、列级权限、条件 SQL、锁版本和事务验收共同保护。

M0 不自动删除订单、资金明细、渠道观察、设备物理结果、inbox、任务尝试、审计、异常、修订、故障、告警或对账证据。以后设置技术保留期时，被渠道 observation、对账 action 或其他业务证据以 `RESTRICT` 引用的 `ops_task_attempt` 必须与引用证据等期保留；清理器只能删除超过保留期且不存在任何引用的尝试。登录会话和其余可清理技术数据另设受控维护身份及批次删除方案；普通业务请求不获得历史清理权限。

#### 逐表落地核对表

下表是迁移详细设计的完整 **83 表**登记，不新增第六套原则。`机构 FK` 均指 D-031 的租户+机构复合外键，`A`=只追加、`S`=当前槽位、`O`=一次补齐/单向收敛、`P`=受保护当前投影、`R`=运行时只读。每张表仍须执行 D-034 的“所有外键显式索引”和机构查询前缀规则；原投递周期候选行已经删除。

##### identity 与 device

| 表 | 关键键、CHECK 与首批索引 | 写类 |
|---|---|---|
| `iam_platform_admin` | UQ 平台管理员公开 UUID、平台命名空间登录名；账号形状 CHECK；IX 启停列表 | P |
| `iam_tenant` | UQ 租户码；状态 CHECK；IX 平台状态列表 | P |
| `iam_organization` | FK tenant；UQ 租户+机构码及候选 `(tenant,id)`；无父机构；IX 租户状态列表 | P |
| `iam_organization_miniapp` | 机构 FK；UQ AppID、每机构一行及机构候选键；激活形状 CHECK | P + 不可变触发器 |
| `iam_staff_account` | 租户 FK；UQ 工作人员公开 UUID、全租户工作人员登录名、租户主体生成槽及租户候选键；IX 租户员工列表 | P |
| `iam_organization_staff_membership` | 机构/员工复合 FK；UQ 机构+员工；IX 员工机构和机构负责人两路径 | P |
| `iam_permission_definition` | UQ 权限码+作用域及 `(id,scope_kind)` 候选键；IX 作用域目录 | R |
| `iam_staff_permission_grant` | 复合 FK 到员工和权限定义；仅 `scope_kind=ORGANIZATION` 时以非空机构列继续复合引用机构任职，租户级授权机构列为空并跳过该 FK；UQ 规范作用域键+活动标记；作用域空值 CHECK；IX 主体活动授权 | O |
| `iam_staff_miniapp_binding` | 机构用户/小程序/员工复合 FK；UQ 绑定公开 UUID及两个活动绑定；状态与撤销字段 CHECK；IX 员工/AppID 当前绑定 | O |
| `iam_organization_user` | 机构/小程序及可空注册部署复合 FK；UQ 机构用户公开 UUID、AppID+OpenID、机构+非空手机号及机构候选键；IX 机构用户列表、地推归因 | P + 不可变触发器 |
| `iam_organization_user_capability` | 机构用户复合 FK；UQ 用户+能力码；IX 机构能力当前列表 | P |
| `iam_platform_login_session` | FK 平台账号；UQ session UUID；时间 CHECK；IX 主体撤销/过期及全局过期 | O |
| `iam_staff_login_session` | 租户员工及可空管理绑定复合 FK；UQ session UUID；Web/小程序列组 CHECK；IX 主体/绑定撤销过期 | O |
| `iam_organization_user_session` | 机构用户/AppID 复合 FK；UQ session UUID；时间 CHECK；IX 用户撤销/过期 | O |
| `dev_device_asset` | UQ 硬件 SN；投口数和生命周期 CHECK；IX 生命周期 | P |
| `dev_device_deployment` | 机构/资产 FK；UQ public code、机构候选键、资产+部署候选键；生命周期 CHECK；IX 机构设备、资产历史 | P |
| `dev_asset_active_deployment` | PK 资产、UQ 部署；复合 FK 同时绑定资产/部署/作用域 | S |
| `dev_port` | 部署复合 FK；UQ 部署+投口号及机构/部署候选键；投口号 CHECK | A |
| `dev_config_version` | 部署复合 FK；UQ 部署+版本及机构候选键；参数范围 CHECK；IX 部署版本倒序 | A |
| `dev_port_config_snapshot` | 配置与投口同部署复合 FK；UQ 配置+投口；单价、满溢阈值、正整数负重量异常阈值（默认 500 克）及等待参数 CHECK | A |
| `dev_config_application` | 配置/部署复合 FK；UQ application UUID、配置版本；状态时间 CHECK；IX 部署状态更新时间 | P |
| `dev_deployment_runtime_state` | PK/FK 部署并保留机构候选键；健康/安全状态 CHECK；清运电磁阀供电、物理门位 `UNKNOWN` 与独立人工关门确认成组，明确无门磁；IX 机构阻断、平台离线扫描 | P |
| `dev_port_runtime_state` | PK/FK 投口且同部署作用域；可空待处理投递 session 使用“部署+投口+session”复合 FK，一投口由单行天然最多一个；传感器/恢复指针形状 CHECK；IX 部署端口/阻断状态。设置/清除只走私有条件更新与统一锁序，不新增业务状态触发器 | P |
| `dev_device_fault_event` | 部署/可空投口复合 FK；UQ fault UUID、活动故障生成键；恢复形状 CHECK；IX 机构活动故障 | O |
| `dev_device_occupancy` | PK 资产；FK 当前部署；分别对 delivery session、clean operation 建直接身份 FK 和同部署/作用域复合 FK（clean 跨模块 FK 后置添加）；两目标恰一的整机互斥 CHECK | S |
| `dev_delivery_session` | 部署/投口/用户/设备配置/投递配置/袋复合 FK；UQ session UUID及供结果/订单回指候选键；单价、负重量异常阈值、30 秒等待、授权/结果期限、阶段和结束原因形状 CHECK；无云端轮次、当前轮次、继续 pending 或轮次序号列；IX 设备活动、用户历史、开始授权和结果恢复超时 | P |
| `dev_device_command` | 部署及五类目标互斥强 FK；UQ command UUID；投递命令只目标 session，类型/目标 CHECK；IX 部署物理状态及目标 | P |
| `dev_edge_event` | 部署/唯一 inbox 复合 FK；UQ event UUID、部署+全局边缘序号、来源 inbox；正序号、Schema/交付类别、时钟质量及摘要形状 CHECK；IX 部署事件时间线、类型/接收时间 | A |
| `dev_device_command_event` | 公共事件头及 command 复合 FK；UQ edge event；投递命令事件强关联 session，禁止本地继续/中间开关门事件类型；事件类型/命令阶段形状 CHECK；IX 命令/部署时间线 | A |
| `dev_physical_result` | 公共事件头、部署/投口及四类目标互斥强 FK；UQ edge event、各目标结果身份，投递 session 一对一；投递首末重量、整场净重、最终负重量异常布尔值及重量可靠性 CHECK，不含中间轮次值；IX 各目标和部署时间线 | A |

##### recycling

| 表 | 关键键、CHECK 与首批索引 | 写类 |
|---|---|---|
| `rec_organization_delivery_config` | 机构 FK；UQ 机构+版本；M0 审核模式、严格负阈值和认定上限 CHECK；IX 机构版本倒序 | A |
| `rec_organization_delivery_config_head` | PK/FK 机构；当前配置同机构+版本复合 FK；版本非负 CHECK | P |
| `rec_organization_order_counter` | PK/FK 机构；末可见序号非负 CHECK | P |
| `rec_delivery_order` | 机构及 session/物理结果/用户/配置/袋复合 FK，且结果目标必须是同一 session；UQ 订单号、机构+可见序号、session、物理结果；当前修订强回指；状态、负重量异常标志/阈值和认定上限 CHECK；IX 待审核、用户、设备/投口、袋及快照游标 | P |
| `rec_delivery_anomaly` | 订单/来源结果复合 FK；UQ 订单+异常码；类别/码 CHECK；IX 机构异常时间 | A |
| `rec_delivery_revision` | 订单复合 FK及复合自 FK；UQ revision UUID、订单+版本、上一版最多一个后继；链和金额差 CHECK；IX 订单版本 | A |
| `rec_delivery_photo` | 订单复合 FK；UQ 订单+四位置、表内非空 photo UUID；状态/照片身份/HTTPS URL/摘要/大小/缺失 CHECK；IX 机构待补照片 | O |
| `rec_organization_clean_config` | 机构 FK；UQ 机构+版本；M0 审核模式/时限 CHECK；IX 机构版本倒序 | A |
| `rec_organization_clean_config_head` | PK/FK 机构；当前配置同机构+版本复合 FK；版本非负 CHECK | P |
| `rec_organization_clean_record_counter` | PK/FK 机构；末可见序号非负 CHECK | P |
| `rec_clean_operation` | 部署/投口/清运员/配置/袋/基准及可空待处理投递 session 复合 FK；UQ operation UUID、活动投口生成键；完成记录强回指；旧袋/基准/授权期限/首次可能通电/电磁阀断电/人工确认及状态 CHECK；IX 状态期限、清运员历史 | P |
| `rec_clean_record` | 操作/物理结果/新旧袋复合 FK；UQ 记录号、机构+可见序号、操作、物理结果；唯一 `review_revision_id` 回指一次初审版本；重量来源/审核状态 CHECK；IX 待审核、袋、完成时间和快照游标 | P |
| `rec_clean_anomaly` | 清运记录复合 FK；UQ 记录+异常码；系统异常 CHECK；IX 机构异常时间 | A |
| `rec_clean_revision` | 清运记录复合 FK；UQ revision UUID、记录一对一；初审结果 CHECK | A |
| `rec_clean_photo` | 清运操作复合 FK；UQ 操作+四位置、表内非空 photo UUID；状态/照片身份/HTTPS URL/摘要/大小/缺失 CHECK；IX 机构待补照片 | O |
| `rec_bag` | 机构 FK；UQ 全平台大小写敏感袋码及机构候选键；URL-safe 长度/字符 CHECK；无状态列 | A |
| `rec_bag_current_occupancy` | PK 袋；UQ 投口、清运操作；复合 FK 保证预留袋/投口等于操作快照；目标 XOR CHECK | S |
| `rec_bag_occupancy_event` | 袋/投口/操作复合 FK；UQ event UUID、清运操作+事件类型；来源形状 CHECK；IX 袋/投口时间线 | A |
| `rec_port_weight_baseline` | 投口/袋/分型来源事件/结果/记录/重测复合 FK；UQ 投口+版本及各来源；非负有效基准/来源 XOR CHECK；IX 投口版本 | A |
| `rec_port_baseline_measurement` | 投口/袋/配置/可空结果/审计复合 FK；UQ measurement UUID、物理结果、活动投口生成键；状态/结果/代际 CHECK；结果基准强回指；IX 待执行和投口历史。设备命令表以唯一强类型目标反向保证每次测量一个命令，本表不重复保存命令指针 | P |
| `rec_port_capacity_state` | PK/FK 投口及机构候选键；当前基准/检测/满溢事件同投口复合 FK；基准、gate、百分比 CHECK；IX 机构容量阻断 | P |
| `rec_fullness_detection` | 投口及来源复合 FK；UQ detection UUID、投递订单/清运来源、活动投口生成键；样本强回指；来源/终态 CHECK；IX 待采样领取、投口历史 | P |
| `rec_fullness_sample` | 检测/物理结果复合 FK；UQ 检测+角色、物理结果；传感器/结论/百分比 CHECK；IX 检测角色 | A |
| `rec_fullness_event` | 投口及各检测复合 FK；UQ event UUID、活动投口生成键；恢复形状 CHECK；IX 机构活动满溢、首次确认 | O |

##### funds 与 operations

| 表 | 关键键、CHECK 与首批索引 | 写类 |
|---|---|---|
| `fund_organization_withdraw_config` | 机构 FK；UQ 机构+版本；M0 金额范围/免审 0 CHECK；IX 机构版本倒序 | A |
| `fund_organization_withdraw_config_head` | PK/FK 机构；当前配置同机构+版本复合 FK；版本非负 CHECK | P |
| `fund_organization_wallet_entry_counter` | PK/FK 机构；末可见序号非负 CHECK | P |
| `fund_user_wallet` | 机构用户复合 FK；UQ 机构用户及机构候选键；冻结和末明细序号非负、投递闸/触发来源/阈值快照形状 CHECK | P |
| `fund_user_wallet_entry` | 钱包及修订/提现/调整强 FK；UQ entry UUID、钱包+严格递增序号、机构+提交可见序号、各来源/阶段；来源 XOR、前后值代数 CHECK；IX 钱包序号时间线、机构快照筛选时间线、来源 | A |
| `fund_wallet_adjustment` | 钱包和分型操作人 FK；UQ adjustment UUID、成功审计；有符号差额和前后值 CHECK；IX 钱包/操作者时间 | A |
| `fund_organization_payout_account` | 机构 FK；UQ 机构一对一及机构候选键；余额/冻结非负 CHECK | P |
| `fund_organization_payout_entry` | 账户及充值/提现强 FK；UQ entry UUID、充值、提现阶段；来源 XOR、金额代数 CHECK；IX 账户时间线、来源 | A |
| `fund_recharge_order` | 机构/创建人 FK；UQ 充值号及机构候选键；费用公式和状态时间 CHECK；IX 机构状态时间、到期扫描 | P |
| `fund_wechat_payment` | 充值/商户/小程序绑定复合 FK；UQ 充值、商户+out trade、商户+可空微信单号；请求快照 CHECK；IX 渠道状态更新时间 | P |
| `fund_wechat_payment_observation` | 支付单及 inbox/attempt XOR 复合 FK；UQ observation UUID、每来源；来源类型 CHECK；IX 支付单时间/外部身份 | A |
| `fund_withdrawal_order` | 用户/钱包/机构账户/配置/商户绑定复合 FK；UQ 提现号及机构候选键；金额快照/状态时间 CHECK；IX 机构状态、用户时间线、长时间未结算 | P |
| `fund_withdrawal_review` | 提现及分型审核人 FK；UQ review UUID、提现一对一、成功审计；决定/操作者 CHECK；IX 审核人时间 | A |
| `fund_active_withdrawal` | PK 钱包、UQ 提现；钱包/提现同作用域复合 FK | S |
| `fund_wechat_merchant_profile` | UQ 商户号；普通商户/启停配置 CHECK；IX 状态 | P |
| `fund_miniapp_merchant_binding` | 机构小程序/商户复合 FK；UQ 小程序/AppID；验证状态时间/锁版本 CHECK；IX 商户状态、机构 | P |
| `fund_wechat_transfer` | 提现/商户/绑定复合 FK；UQ 提现、out bill、可空 transfer bill；固定请求/内部终态形状 CHECK；IX 渠道状态更新时间、双单号 | P |
| `fund_wechat_transfer_observation` | 转账单及 inbox/attempt XOR 复合 FK；UQ observation UUID、每来源；来源类型 CHECK；IX 转账时间/外部身份 | A |
| `fund_payout_gate` | PK/FK 商户；当前暂停事件同商户强回指；状态形状 CHECK；IX 状态 | P |
| `fund_payout_gate_event` | 商户/触发观察/恢复原事件/管理员 FK；UQ event UUID、触发观察、原暂停恢复一次；事件列组 CHECK；IX 商户时间线 | A |
| `ops_inbox_message` | 作用域 FK；UQ inbox UUID、来源主体+消息 ID；状态/计数 CHECK；IX 待处理、作用域时间 | P |
| `ops_message_quarantine` | 可空冲突 inbox/确认审计 FK；UQ quarantine UUID、去重键；作用域/状态及非负锁版本 CHECK；IX 状态原因时间 | O |
| `ops_reliable_task` | 可空 inbox/命令强 FK；UQ task UUID、task key、每 inbox/命令；作用域/租约/wake CHECK；候选领取索引须以真实领取 SQL、EXPLAIN 和并发测试定稿 | P |
| `ops_task_attempt` | task 复合 FK并复制作用域；UQ attempt UUID、task+序号、lease token；调用/接管/结果形状 CHECK；IX task 时间线 | O |
| `ops_audit_log` | 分型主体 FK；UQ audit UUID、request UUID、成功 operation 生成槽；操作者/结果 CHECK；IX 作用域、动作、操作者时间 | A |
| `ops_alert` | 可空确认审计 FK；UQ alert UUID、永久来源、活动聚合生成键；作用域/恢复 CHECK；IX 作用域活动严重级 | O |
| `ops_reconciliation_run` | 商户/协调任务 FK；UQ run UUID、商户+业务日、任务；平台作用域/状态计数 CHECK；IX 状态、商户日期 | P |
| `ops_reconciliation_issue` | first/latest run、处理审计 FK；UQ issue UUID、未解决 dedupe 生成键；作用域/解决形状 CHECK；IX 作用域活动严重级 | O |
| `ops_reconciliation_action` | issue 及 run/audit/attempt XOR FK；UQ action UUID、action key；来源/作用域 CHECK；IX issue/run/task 时间线 | A |
