# EcoBin P0 目标数据库设计：身份与设备字段（D-011～D-015）

> 总索引：[database-design-draft.md](../database-design-draft.md)
>
> 状态：**D-011～D-015 已确认**
>
> 说明：本文件是数据库设计草案的分章正文，与其余章节共同组成一份设计；决策编号与原正文保持不变。

## 7. 第三批已确认：公共字段、组织身份与设备经营字段

本批把已经确认的身份和设备表清单落实为可建表的核心列与约束。这里确认的是数据边界，不表示开始编写 Flyway；枚举的数据库英文值可以在详细设计时统一命名，但不得改变本节表达的状态含义。

### D-011 公共列型、字符集、时间与删除规则

**已确认：采用统一删除政策而不是所有表统一删除字段；显式业务状态、不可变事实、当前槽位和技术数据按类别处理，软删除只对未来确有删除/恢复用例的表按表启用。**

- 独立实体和事实的内部代理主键统一为带符号 `BIGINT AUTO_INCREMENT`；一对一投影和当前占位关系允许直接以所属对象的 `BIGINT` 外键为主键。金额继续按 D-005 使用带符号 `BIGINT` 分，设备原始重量使用带符号 `BIGINT` 克，单价使用 `DECIMAL(15,4)` 元/kg。业务数量和序号即使业务上非负也不使用 MySQL `UNSIGNED`，非负范围交给 `CHECK`，避免 JDBC/Python 类型边界不一致。
- 跨端 UUIDv4 首期统一保存为 `CHAR(36) CHARACTER SET ascii COLLATE ascii_bin`；公开码、渠道单号、AppID、OpenID、硬件 SN 和状态码等技术标识使用定长或有上限的 ASCII 二进制排序列，大小写敏感。登录名、租户码等人工作为键输入的标识保存规范化小写值，并用 `CHECK(value = LOWER(TRIM(value)))` 防止 seed、导入或遗漏规范化的写路径绕过唯一键。名称、地址、备注等自然文本使用 `utf8mb4`。
- 所有业务时间使用 UTC `DATETIME(3)`；数据库 session、JDBC 连接、Python 连接和后台任务时区都显式设为 UTC，界面才转换为 Asia/Shanghai。设备原始发生时间和后端接收时间分别保存，不用设备时间决定幂等、资金终态或任务租约。`created_at` 表示入库时间，需要并发条件更新的主数据或当前投影另有 `updated_at` 和 `lock_version BIGINT`；只追加事实不伪造 `updated_at`。
- 布尔值使用 `TINYINT` 并加 `CHECK IN (0,1)`；内部有限状态使用大写 ASCII `VARCHAR` 加 `CHECK`。微信、OneNet 等外部错误码不做封闭枚举，避免渠道新增值导致可信消息无法落库。MySQL 8.0.16 是 `CHECK` 实际执行的能力来源下限；D-041 已把唯一发布和验收版本固定为 MySQL 8.4 LTS，所有真实迁移负测均在锁定 digest 的目标 8.4 执行。
- 机构级父表提供包含 `tenant_id + organization_id + id` 的候选唯一键，子表使用复合外键验证完整作用域；每个外键和高频查询条件建立以过滤列开头的索引。所有业务外键默认 `ON DELETE RESTRICT`，不级联删除或更新历史归属。
- 删除策略不为所有表统一添加 `deleted`：租户/账号使用禁用，用户使用冻结，权限使用撤销，部署使用结束，资产使用报废，配置使用版本替换；订单、账本明细、渠道观察、物理结果和操作审计等只追加事实本身不允许业务删除。未来确有“从日常界面消失且允许恢复”用例的表，必须单独确认软删除字段、唯一键、恢复条件及审计，不启用 MyBatis-Plus 全局逻辑删除字段。
- JSON 只用于原始/规范化协议载荷、不可变请求快照或低频诊断扩展；租户、机构、状态、金额、重量、作用域、幂等 ID、渠道单号和所有参与检索/约束的字段必须结构化。摘要统一使用 `BINARY(32)` SHA-256，密码使用可升级的自适应哈希字符串，任何密钥和令牌正文都不落业务表。
- 操作人不在所有表机械复制一个无外键 `created_by`；需要证明敏感操作主体时由对应业务命令事实保存强类型操作者，并关联 `ops_audit_log`。初始化 seed 通过明确的系统来源标识，不伪造工作人员 ID。

#### D-011 删除策略说明

原方案不采用“所有表统一 `deleted` 并由默认查询隐藏”，原因如下：

1. EcoBin 大部分所谓“删除”其实已经有不可互换的业务语义：租户/机构/账号是禁用，用户是冻结，权限是撤销，部署是结束，资产是报废，配置是新版本替换。一个 `deleted=1` 无法说明对象为什么不可用、能否恢复，以及恢复前需要满足什么条件。
2. 订单、订单认定版本、钱包和机构资金明细、充值/提现渠道观察、设备物理结果、清运/袋历史、inbox 和操作审计是持续参与查询、追溯或对账的事实。若 MyBatis-Plus 默认追加“未删除”条件，误标一行就会让余额重算、渠道对账或事故调查少掉证据；软删除也不能代替保存操作者、原因和前后值的审计事实。
3. 通用布尔删除位与唯一键冲突。例如全平台唯一登录名若使用 `UNIQUE(login_name, deleted)`，同名账号第二次删除时会与第一条已删除记录冲突；若唯一键仍只有 `login_name`，删除后又不能重新创建。可以用 `deleted_at + active_slot` 生成列解决“最多一个当前记录”，但每个需要复用的业务键仍要单独设计，恢复时也必须处理当前占位冲突，已经不再是一个真正通用的字段方案。
4. 外键只知道父行是否存在，不知道父行是否已软删除。父记录被标记删除后，数据库仍会认为子记录引用合法；反过来，默认过滤又会让应用查询看起来像产生孤儿。租户/机构/钱包/部署等作用域关系最终仍必须依靠显式状态和业务事务校验。
5. `dev_asset_active_deployment`、`dev_device_occupancy`、`rec_bag_current_occupancy` 和 `fund_active_withdrawal` 是保证“当前唯一”的锁槽，不是历史表。软删除后主键/唯一键仍被旧行占用；若为了软删除把它们改造成多行 active-slot 模型，会增加并发锁和误恢复风险。正确方式是在业务事务中物理插入/释放槽位，历史由部署、投递会话、清运操作和提现单保存。
6. 软删除不是数据库误操作恢复方案：误执行批量 `UPDATE deleted=1` 与误删除同样可能发生，而且直接改回标志可能违反当前唯一键、资金状态或设备归属。生产误操作恢复仍依赖最小权限、审计、备份/PITR 和受控补偿。

**当前推荐的折中方案**是统一“删除政策”而不是统一“删除字段”：

| 数据类别 | 建议处理 |
|---|---|
| 租户、机构、账号、用户、资产、部署、权限关系等业务主数据 | 使用禁用、冻结、撤销、结束、报废等显式状态；只有状态机允许时才恢复。 |
| 订单、资金、渠道、设备结果、清运、配置版本和审计等不可变事实 | 不提供删除或软删除；以后通过数据库写权限和只追加用例保护。业务纠错追加新事实，不隐藏旧事实。 |
| 当前部署、整机占位、袋占位和进行中提现槽位 | 在所属业务事务中物理创建/释放，当前槽位不承担历史保存。 |
| 会话、任务尝试、临时技术载荷等有保留期的数据 | 到期后由受控清理任务物理删除；软删除不会节省空间，也不能替代保留策略。 |
| 尚未启用且没有任何引用的误建草稿/配置 | 按具体表决定允许物理删除或增加 `archived_at`；不能因为少数草稿场景给全部事实表增加删除语义。 |

以后新增软删除用例时采用“按表显式启用”而不是 MyBatis-Plus 全局字段：仅对确有“从日常界面消失且允许恢复”用例的可变主数据增加 `deleted_at/deleted_by/delete_reason`；唯一业务键使用专门的 active-slot 生成列；恢复走服务用例重新校验唯一键、父级状态和机构作用域。资金、订单、渠道证据、设备结果与审计表始终属于不可软删除例外。

### D-012 租户、机构、工作人员与权限字段

**已确认：Web 工作人员采用“全平台唯一登录名 + 密码”，不输入租户编码；有权限的机构或租户人员在 Web 中把工作人员账号与对应机构用户显式绑定，之后该微信用户免密自动进入当前机构管理页。P0 信任已获绑定权限的员工，不按目标账号权限等级限制代绑。**

| 表 | P0 核心字段与约束 |
|---|---|
| `iam_platform_admin` | `id`、全局唯一不可变 UUIDv4 `platform_admin_uid`、全局唯一规范化 `login_name`、`password_hash`、`display_name`、`enabled`、连续失败次数、锁定截止时间、`auth_version`、密码修改时间、锁版本及创建/更新时间；平台使用独立登录入口，不伪装成租户账号。 |
| `iam_tenant` | `id`、全局唯一稳定且规范化的 `tenant_code`、企业名称、启停状态、可空联系人/电话/地址、锁版本及时间列；不混入密码、小程序、机构或资金字段。 |
| `iam_organization` | `id + tenant_id`、租户内唯一稳定且规范化的 `organization_code`、机构名称、启停状态、可空联系电话/地址、锁版本及时间列；没有 `parent_id`，不能形成多级机构。 |
| `iam_staff_account` | `id + tenant_id`、全局唯一不可变 UUIDv4 `staff_account_uid`、`account_kind=TENANT_PRINCIPAL/STAFF`、全平台唯一规范化 `login_name`、密码哈希、展示名、可空联系电话、启停/登录锁定/`auth_version`、锁版本及时间列。数据库以“主体槽位”唯一键保证每租户最多一个主体账号；租户启用前由用例校验必须已经存在一个主体账号。 |
| `iam_organization_staff_membership` | `id + tenant_id + organization_id + staff_account_id`、`is_manager`、启停、`lock_version` 及时间列；同一员工在同一机构只能有一条当前任职，同一员工可以任职多个机构，一个机构可以有多个负责人。 |
| `iam_permission_definition` | `id`、稳定权限码、`scope_kind=TENANT/ORGANIZATION`、名称、说明及启停；唯一 `(permission_code, scope_kind)`，并提供候选键 `(id, scope_kind)` 供授权表建立复合外键。同一能力若允许两个作用域就由迁移建立两条定义，避免使用无法被跨表 `CHECK` 验证的“允许作用域”布尔值。 |
| `iam_staff_permission_grant` | 租户、工作人员、权限定义、与定义共同组成复合外键的 `scope_kind`、可空机构、授予/撤销时间；租户级机构必须为空，机构级必须非空且要求员工在该机构任职。生成非空作用域键参与唯一约束，防止 MySQL 的 `NULL` 允许重复授权。 |
| `iam_staff_miniapp_binding` | 机构用户与工作人员账号之间一条显式关系，保存全局唯一不可变 UUIDv4 `binding_uid`、`tenant_id + organization_id + organization_miniapp_id + organization_user_id + staff_account_id`、`ACTIVE/REVOKED`、绑定/撤销时间、撤销原因和锁版本；同一机构用户最多绑定一个当前工作人员账号，同一工作人员在同一 AppID 下最多绑定一个当前机构用户。 |

- Web 工作人员登录请求只携带 `login_name + password`；`login_name` 在全部租户工作人员之间全平台唯一，不能由两个租户重复使用。登录名入库前统一 `trim + lowercase`，展示名保持原样。平台管理员仍使用单独入口和账号命名空间，因此是否允许与租户工作人员同名不影响身份解析。
- 登录前尚无可信 `tenant_id`，因此 identity 模块提供唯一的内部预认证查询端口，显式绕过租户拦截并按规范化全局登录名只读取账号 ID、租户 ID、密码哈希、账号/租户状态、锁定信息和 `auth_version`；它不返回业务数据，也不能被普通 Mapper/Controller 复用。密码校验成功后才建立租户上下文，其他工作人员查询仍必须经过租户拦截。
- 租户主体账号天然拥有本租户全部能力，机构负责人天然拥有本机构全部能力，两者不生成成批授权行；普通工作人员的总部能力使用租户级授权，机构能力使用机构级授权。同一账号可以同时获得客服、审核等多项能力。
- 授权关系自身不保存一个无法建立真实外键的通用操作者 ID；平台管理员或工作人员的具体授予/撤销动作由具有分型操作者字段的 `ops_audit_log` 留痕。授权变化在同一事务递增工作人员 `auth_version` 并撤销其现有会话。
- “恰好一个租户主体”不能只靠允许租户先存在的单表外键完全表达：数据库保证最多一个，租户启用事务和一致性巡检保证至少一个。P0 初始租户与主体账号由受控 seed 建立，禁止在 Flyway 中写固定弱密码或固定密码哈希。
- 已确认小程序同时提供工作人员管理入口，但首期只展示当前 AppID 对应机构的精简统计、设备容量/满溢和告警，不提供总部跨机构切换，也不开放 I-030 的检测或恢复写命令；数据权限仍取自 `iam_staff_account + membership/grant`，不能把 C 端用户的清运能力当作后台权限。
- **已确认的 P0 首次绑定方案**：目标工作人员先正常进入对应机构小程序，系统通过 `wx.login` 建立 `iam_organization_user`；为了让 Web 操作者可靠找到本人，绑定前要求该机构用户已经验证手机号。具备人员绑定权限的机构人员可以在本机构内、租户主体或具备租户级人员绑定权限的总部人员可以在租户全部机构内，通过 Web 明确选择“工作人员账号 + 对应机构用户”并确认绑定。Web 展示昵称和脱敏手机号辅助确认，不展示完整 OpenID，也不根据手机号自动绑定。
- P0 不按目标工作人员账号的权限等级增加绑定限制：上述操作者可在自己的绑定管理作用域内设置普通工作人员、机构负责人、总部员工或租户主体账号。项目负责人接受首期员工可信带来的代绑风险；系统仍校验操作者具有绑定权限和目标机构范围，并完整审计绑定、换绑和撤销。
- 绑定事务必须校验机构用户确实属于所选 AppID/机构、工作人员属于同一租户，并且工作人员具备访问该机构的有效路径；操作者也必须具有对应范围的人员绑定权限。绑定使用 active-slot 生成列保证“同一机构用户最多一个当前工作人员账号”和“同一工作人员在同一 AppID 下最多一个当前机构用户”，并用复合外键保证用户、AppID、机构和工作人员作用域一致。
- 后续进入时，后端以 `wx.login` 得到的当前 AppID/OpenID 找到机构用户，再查找唯一有效工作人员绑定；绑定、工作人员及当前访问权限都有效时，自动创建 `iam_staff_login_session`，其 `client_kind=MINIAPP_MANAGEMENT` 并固化 `staff_miniapp_binding_id + organization_miniapp_id + active_organization_id`，返回 `aud=miniapp-staff` 会话并默认进入管理页，全程不校验工作人员密码。建会话事务必须锁定并再次确认绑定仍为 `ACTIVE`；每次管理请求也继续复核绑定状态，避免撤销与并发登录竞态重新生成有效会话。没有有效绑定或权限已失效时只建立普通机构用户会话并进入普通页面。
- 绑定只复用机构用户作为稳定的 AppID/OpenID 身份，不合并两类权限和状态：普通用户冻结、手机号修改、钱包或清运能力不自动改变工作人员管理权限；管理权限只由绑定、工作人员账号、任职和授权决定。若微信身份不再可信，必须在 Web 中撤销绑定。
- 禁用工作人员、停用租户/机构/AppID、撤销绑定，或工作人员权限/任职变化时立即使对应管理会话失效。换绑必须在同一事务内撤销旧绑定及其管理会话并创建新绑定；已撤销记录不能恢复为 `ACTIVE`。一个工作人员可以分别绑定多个机构 AppID 下的微信身份，但首期每次会话只能管理当前 AppID 对应机构，不能跨机构切换。
- 该方案只给已确认的 D-007 identity 清单新增一张强类型绑定关系表，不再增加一次性邀请表，也不把工作人员 ID 直接塞入 `iam_organization_user` 或把管理权限编码成普通用户 capability。绑定/撤销保留历史并记录操作者、时间、目标和结果；撤销绑定时同一事务撤销引用该绑定的全部管理会话。绑定只证明微信身份对应哪个工作人员，不授予任何权限；本周管理端保持只读统计、设备容量/满溢和告警，未来开放价格、资金、恢复或人员等敏感写操作时再确认是否需要重新认证。
- **接口设计落实补充（I-009）**：`platform_admin_uid`、`staff_account_uid` 和 `binding_uid` 是客户端会话、URL 与命令使用的公开身份，创建后不可修改且不得复用；内部关联仍只使用 `BIGINT` 主键和复合外键。登录名、手机号或显示名都不能替代这些公开身份。

### D-013 小程序用户、附加能力与登录会话字段

**已确认：AppID 是机构入口，OpenID 是该 AppID 下的微信身份，手机号在同一机构内唯一；AppID 一旦产生用户或资金事实，P0 不允许原地替换。注册来源设备是用于地推成果统计的可选归因，直接进入小程序仍允许注册；注册时间是首次 `wx.login` 成功并创建机构用户记录的后端时间。**

| 表 | P0 核心字段与约束 |
|---|---|
| `iam_organization_miniapp` | `id + tenant_id + organization_id`、全局唯一 `appid VARCHAR(32) ASCII BINARY`、展示名、登录启停、`secret_ref`、一次性 `activated_at`、锁版本及配置/更新时间；唯一 `(tenant_id, organization_id)`，每机构 P0 只有一个当前 AppID。AppSecret 明文、密文和临时 access token 均不存本表，只保存外部秘密来源引用。 |
| `iam_organization_user` | `id + tenant_id + organization_id + organization_miniapp_id`、全局唯一不可变 UUIDv4 `organization_user_uid`、`openid VARCHAR(64) ASCII BINARY`、可空规范化 `phone_e164` 及首次绑定时间、可空昵称/头像、`status=ACTIVE/FROZEN`、`auth_version`、锁版本及创建/冻结时间；新增不可变且非空的 `registered_at` 与可空但创建后不可变的 `registered_via_deployment_id`。唯一 `(organization_miniapp_id, openid)` 和 `(tenant_id, organization_id, phone_e164)`。不保存密码、余额、真实姓名或用于跨机构合并身份的 UnionID。 |
| `iam_organization_user_capability` | 用户、稳定能力码、启用/授予/撤销时间；唯一 `(tenant_id, organization_id, organization_user_id, capability_code)`，P0 预置 `CLEAN_OPERATION`。清运能力不把用户转换为工作人员，也不改变其普通用户历史；操作者由分型审计字段留痕。 |

- 同一手机号可以在 A、B 两个机构分别注册并拥有完全隔离的钱包；同一机构不能用一个手机号建立多个钱包用户。号码按国家码规范化为 `phone_e164` 后再建立唯一键，避免 `138...` 与 `+86138...` 绕过约束。新 OpenID 尝试绑定本机构已有手机号时不能新建第二个钱包，只能走以后明确的安全换绑/恢复流程。
- 注册来源引用 `dev_device_deployment`，不能只引用可在机构间调拨的 `dev_device_asset`。首次 `wx.login` 创建机构用户时若携带设备二维码上下文，先验证该部署与当前 AppID、租户和机构一致，再通过 `(tenant_id, organization_id, registered_via_deployment_id)` 可空复合外键锁定历史作用域；直接进入小程序创建用户时该字段为 `NULL`。
- `registered_via_deployment_id` 与 `registered_at` 在首次创建机构用户的同一事务中一次确定；无论设备来源初值是部署 ID 还是 `NULL`，之后都禁止补填或修改。后续扫码、设备调拨或重新部署不能改写地推归因。P0 不额外增加 `registration_source_kind`，空值明确表示“无设备来源”。
- 上述不可变性同时由数据库写时约束保证：插入时必须由后端生成非空 `registered_at` 并一次决定可空来源；更新时使用 NULL-safe 比较禁止注册时间或来源发生任何变化。重复 `wx.login` 幂等返回既有用户，不能重新读取本次二维码上下文覆盖归因；手机号首次绑定、换绑、重新登录或恢复账号都不能改变注册时间和设备来源。
- 地推成果查询以 `(tenant_id, organization_id, registered_via_deployment_id, registered_at)` 为主要过滤/分组维度并建立配套索引；每个机构用户只计一次，空来源单列且不归入任何设备。通过部署可以还原注册时机构/地点并关联物理资产，不需要重复保存易漂移的设备名称或地点快照。
- `created_at` 是通用技术审计列，`registered_at` 是地推统计使用的明确业务列；二者都在首次 `wx.login` 成功创建机构用户的同一事务中使用服务端统一 UTC 时间写入，通常相等，但不能用小程序或设备上报时间。未绑定手机号的用户也进入注册统计；`phone_bound_at` 单独记录首次绑定手机号的时间。
- 该归因只提供地推成果统计事实，不能单独证明现场到访；P0 不根据它自动生成应付款或资金结算。若以后要自动结算，必须另行设计活动、推广人员归属、反作弊和人工核验规则。
- `iam_organization_user` 的 `organization_miniapp_id + openid` 创建后不可修改；未来如需微信身份恢复或换绑，必须先撤销其工作人员绑定和相关管理会话，再通过显式流程建立新机构用户或重新绑定，不能通过更新原行把管理身份静默转移给另一个 OpenID。
- AppID 在允许登录、绑定支付或建立首个用户前执行一次不可逆激活；激活事务写入 `activated_at`。数据库 `BEFORE UPDATE` 触发器使用 NULL-safe 比较，只允许 `activated_at` 从 `NULL` 变化为一次非空值；激活后该时间以及 `appid/tenant_id/organization_id` 永远不可再变化。未激活的配置错误才允许纠正并记录审计。确需更换已使用 AppID 时必须先设计用户身份和钱包迁移，作为后续前向迁移处理，P0 不猜测合并。
- **接口设计落实补充（I-012）**：完整 AppSecret 继续只保存在 `secret_ref` 指向的外部秘密设施，不因允许授权 Web 回显而进入业务表。有当前机构 `miniapp.manage` 能力的人员可通过配置详情接口从秘密设施读取完整值；业务数据库只保存引用和配置事实，日志与审计只保存脱敏值，不保存回显正文。
- **接口设计落实补充（I-014）**：机构任职增加 `lock_version` 承接负责人、启停和机构权限完整集合替换的 `expectedVersion`；租户级权限集合继续以工作人员 `auth_version` 作为并发版本。任职和授权使用 `organization_code + staff_account_uid` 公开复合路径，不新增任职或授权公开 UUID。
- **接口设计权限目录补充（I-014）**：V10 权限参考数据写入本章已冻结的身份目录能力，其中 `organization-manager.manage` 只存在 `TENANT` 作用域，明确代表可任命/撤销机构天然全权负责人；普通 `permission.manage` 不能隐含取得该权力。其余允许双作用域的能力分别建立 `TENANT/ORGANIZATION` 定义，不用运行时自创权限码。
- 三张登录会话表共同保存唯一 `session_uid`（同时作为 JWT `jti`）、强类型主体复合外键、签发/过期/撤销时间、撤销原因、登录 IP 二进制值、User-Agent 摘要和 `auth_version` 快照；JWT 以不同 `aud` 选择对应会话表，不保存 JWT、Cookie、微信 code、access token 或 refresh token 明文。平台、工作人员和机构用户会话分别带其真实作用域。工作人员会话以 `CHECK` 区分客户端：`WEB` 时绑定/AppID/当前机构均为空；`MINIAPP_MANAGEMENT` 时三者均非空，并由包含租户、工作人员、机构和 AppID 的复合外键保证会话与绑定属于同一作用域。
- 每次鉴权同时检查会话与账号、租户、机构、AppID、任职/授权或清运能力当前状态；禁用、改密、撤销任职/授权或清运能力时事务性撤销受影响会话。完整手机号和 OpenID 不写入应用日志或操作日志展示字段。
- **接口设计落实补充（I-009）**：`organization_user_uid` 是小程序 `sub`、Web 人工绑定和机构用户资源使用的唯一公开身份，创建后不可修改；完整 OpenID、手机号和内部 `BIGINT id` 均不能作为客户端资源 ID。
- **接口设计并发补充（I-009）**：设置或换绑必须同时比较工作人员在当前 AppID 下、目标机构用户两侧的活动绑定快照；显式 `null` 表示预期无绑定，非空快照使用已有 `binding_uid + lock_version`。事务按稳定顺序锁定两侧主体，匹配后才撤销冲突行并创建新绑定；数据库活动槽冲突统一转为业务版本冲突，不能让并发管理员静默覆盖。

微信渠道中的 OpenID 从属于 AppID，且商家转账请求的 AppID 必须与取得该 OpenID 的 AppID 一致；数据库因此不建立“全平台 OpenID 用户”，也不能只凭 OpenID 跨机构找钱包。

### D-014 物理设备、部署、投口与配置字段

**已确认：物理资产只记录平台公开设备身份，机构归属只记录在不可变部署中；二维码、配置与设备侧密钥分别处理。**

| 表 | P0 核心字段与约束 |
|---|---|
| `dev_device_asset` | `id`、全局唯一不可变 `hardware_sn`、型号、可空生产批次、预期投口数 `1..6`、资产生命周期 `IN_STOCK/ALLOCATED/IN_USE/MAINTENANCE/RETIRED`、报废时间/原因和锁版本；报废时间不得早于建档时间。不保存租户、机构、OneNet 产品/设备身份、设备 Key 或秘密引用。P0 的产品 ID 来自当前后端部署配置，OneNet 设备名由 `hardware_sn` 确定性推导。 |
| `dev_device_deployment` | `id + tenant_id + organization_id + asset_id`、至少 128 位 CSPRNG 生成且全局唯一的 `public_code`、部署生命周期 `PENDING_INSTALL/COMMISSIONING/ENABLED/MAINTENANCE/DISABLED/ENDED`、独立后台启停位、调试/启用/结束时间、结束方式与原因、锁版本；资产、租户、机构和公开码创建后不可修改，已结束实例不可恢复。 |
| `dev_asset_active_deployment` | 以 `asset_id` 为主键，唯一引用当前部署并重复固化作用域；待安装、调试、启用、维护或停用仍占用该关系，只有部署进入 `ENDED` 才删除。数据库因此阻止同一资产并存两个当前部署。 |
| `dev_port` | `id + tenant_id + organization_id + deployment_id`、`1..6` 的 `port_no` 和创建时间；唯一 `(deployment_id, port_no)`，创建用例保证投口数量和资产声明一致。展示名称、启停、价格和满溢规则不放主表，全部来自版本化配置。 |
| `dev_config_version` | 部署、单调递增版本号（`1..9007199254740991`）、配置 schema 版本、设备展示名、地址、经纬度、边缘/MCU 心跳间隔与漏报阈值、关门重试、M0 固定 30 秒的本地继续等待、设备级负重量阈值（默认 500 克）、投递自动关门/称重超时、清运电磁阀脉冲和烟雾监控开关；同时保存完整配置 `content_sha256` 与 MCU 子集 `mcu_payload_sha256`、发布人和发布时间。一经发布只读，唯一 `(deployment_id, version_no)`；后端固定 60 秒首次作业授权期限不属于租户配置。进入当前 F-10 `deviceConfig` 的数值严格使用 u32 边界。 |
| `dev_port_config_snapshot` | 配置版本、投口、1～32 字符且无首尾空白的展示名、后台启停、通用回收单价、`INFRARED_ONLY/WEIGHT_ONLY/INFRARED_OR_WEIGHT` 满溢判断、配置满载克数、投递/满溢等待、门自动关闭、称重稳定窗口/允许波动/样本数/超时/量程、校准版本、红外采样和投递门动作超时；唯一 `(config_version_id, port_id)`。当前 F-10 字段按 u32/u16/i32 精确限界，单价乘 `10000` 后必须为 `1..4294967295`。负重量阈值只在设备级配置保存；暂停免费回收应停用投口而不是配置 0 元。 |
| `dev_config_application` | 全局唯一 `application_uid`、部署、配置版本、`PENDING/EDGE_SAVED/APPLIED/FAILED`、设备报告的版本号、完整配置摘要和 MCU 子集摘要、`edge_persisted_at`、`mcu_synced_at`、`applied_at`、最近失败时间/失败码和锁版本；同一配置版本只有一条应用过程。三个设备报告值成组并以复合外键精确引用期望配置；`EDGE_SAVED/FAILED/APPLIED` 均携完整三元组。失败证据由只追加命令事件完整保存，当前投影的最近失败信息在重同步及后来成功后仍保留。只有三元组精确匹配、香橙派可靠落盘且 MCU 同步时间成立才允许 `APPLIED`。 |

- 用户投递二维码由 `iam_tenant.tenant_code + deployment.public_code` 形成，不保存整段二维码 URL，也不包含数据库连续主键、硬件 SN、OneNet 身份、设备密钥或安装密钥。后端解析后仍校验部署机构与当前 AppID/用户机构一致；重新部署生成新 `public_code`，旧码只能得到“原实例已停用”。
- 设备名称、地址文字修正、坐标微调和投口展示名修改创建新配置版本；实际迁址仍结束旧部署并创建新部署。配置发布与 `PENDING` 应用记录在同一事务写入，并创建唯一可靠下发任务。
- `dev_config_version.content_sha256` 覆盖上述全部设备级字段以及按 `port_no` 排序的全部投口快照；`mcu_payload_sha256` 独立覆盖实际下发 MCU 的严格子集。二者都排除数据库主键、发布时间、操作者和临时凭证。部署运行投影保存已应用的 `version_no + content_sha256 + mcu_payload_sha256` 并复合引用原配置。最高已发布版本不等于设备最高已应用版本时，禁止授权新投递和新清运；已经开始的作业继续使用其冻结旧版本。`FAILED` 允许重同步同一应用/命令/任务，不创建新配置版本，技术尝试只写 `ops_task_attempt`。
- **当前 F-10 下发边界**：机器契约 `deviceConfig` 只包含本地继续等待、负重量阈值、投递自动关门、设备称重超时、清运电磁阀脉冲和烟雾监控；`portConfig` 包含展示名/启停/单价、满溢规则以及称重、校准、红外和投递门动作参数。设备展示名、地址和坐标是中心元数据，不进入该 payload。边缘/MCU 心跳与漏报阈值、关门重试、投递沉降等待和投口门自动关闭等虽保存在中心完整配置中，但冻结的 F-10 payload 没有对应属性；F-04 只证明其版本化存储，不能宣称已经下发或生效。固定帧模式无法投影的设备执行项由 F-11 显式标记本地生效、不支持或未知；以后接线前必须先修订机器契约，且不得提前纳入 `mcu_payload_sha256`。
- 设备 Key 只配置在对应香橙派，后端 OneNet 下行只使用部署环境注入的产品级 AccessKey；P0 资产表不保存设备 Key 或其引用，也不重复保存由系统产品配置和 `hardware_sn` 可推导的 OneNet 公开映射。OneNet 产品级 AccessKey、COS/APIv3 密钥及 COS 临时凭证也不写入资产、配置、命令载荷、任务快照或日志，执行时从部署环境加载或即时生成。
- P0 不提供 OneNet 产品 ID 的运行时切换；该部署纪元内配置必须稳定。以后迁移产品时需以前向设计保留旧来源身份和迟到消息映射，不能靠改当前配置覆盖历史事实。
- `ALLOCATED` 保留已冻结的资产生命周期含义，但 M0 不开放独立分配用例：试点设备由平台受控建档后直接建立机构部署。M1 实现分配流程时新增租户分配事实及历史，不能仅靠资产状态码表达“分配给谁”。
- D-008 已确认的 M0 表清单不含安装码、正式验收记录或调拨记录表。本周试点设备使用受控 seed/后台建档完成首次绑定，不实现自助安装码和完整生命周期界面；但启用真机前仍必须完成 P0 的 OneNet、MCU、门控、称重、相机/COS、断网恢复和安全故障检查，以受控验收报告/测试证据及操作审计留痕。M1 再增加正式安装凭证、逐项验收领域记录和完整调拨/替换流程，不能把真机安全验收本身延期。
- **接口设计落实补充（I-016/I-017）**：平台资产登记只写本地 `IN_STOCK` 事实，不调用 OneNet 或验证在线。M0 直接部署事务锁定库存资产并同时创建 `COMMISSIONING` 部署、当前部署槽、全部 `1..N` 投口、`UNKNOWN` 且初始安全锁存的运行投影，再把资产推进为 `IN_USE`；不顺带创建默认配置。
- **接口设计落实补充（I-018）**：权限目录新增允许租户/机构双作用域的 `device.read`、`device.manage` 和 `device.configuration.manage`。部署激活与经营开关使用部署 `lock_version`；运行投影有独立版本并在事务内重新检查，`deliveryAllowed/cleaningAllowed` 仍不落库。
- **接口设计权限补充（I-026～I-030）**：V10 为 `clean.read`、`device.detection.execute` 和 `device.recovery.execute` 分别建立 `TENANT/ORGANIZATION` 定义；清运初审继续复用 `review.execute`。工作人员小程序只将当前机构 `device.read` 的容量/满溢安全摘要加入渠道白名单，不放行三项写命令。
- **接口设计权限补充（I-036～I-040）**：V10 为 `audit.read`、`alert.read`、`alert.acknowledge`、`reconciliation.read`、`reconciliation.handle` 和 `statistics.read` 分别建立 `TENANT/ORGANIZATION` 定义。平台任务恢复、隔离确认和平台对账运行属于平台固定能力，不伪装成租户权限码；工作人员小程序渠道白名单只增加当前机构 `alert.read` 与 `statistics.read`，不放行确认告警、对账处置、审计或技术任务接口。
- **接口设计落实补充（I-019/I-020/F-10）**：发布请求的设备级和投口级字段按上表完整版本化，负重量阈值位于设备级；正式配置进度以 `version + contentSha256 + mcuPayloadSha256` 三元组证明。配置发布以最高 `version_no` 承接 `expectedLatestVersion`；更高版本使旧应用派生为非当前期望，但不改写其真实应用状态。自动重试耗尽只使 `ops_reliable_task=BLOCKED`，不得伪造应用 `FAILED`。重同步复用原应用、设备命令和任务；可信迟到证明仍按真实版本归并并保留历史失败证据，但只有最高期望版本精确 `APPLIED` 才解除新作业阻断。

### D-015 运行健康、占位、投递会话与设备证据字段

**已确认：当前运行态是可由设备证据核对的健康与安全投影，物理执行权用强类型占位保证，跨网络作业依靠稳定 ID、序号和摘要恢复。**

- `dev_deployment_runtime_state` 以部署为主键，除中心派生的在线、整机安全、称重聚合、相机/存储/时钟健康外，无损保存 F-10 整机快照的 edge boot/version、MCU boot/firmware、精确 UART 状态和 major/minor、16 位 capability bitmap、已应用配置三元组、本地存储状态、时钟状态和待确认可靠事件数。`dev_port_runtime_state` 以投口为主键，无损保存每个投口的投递门状态/健康、清运锁供电/电磁阀健康、清运员关门确认、称重、红外值/健康和烟雾值/健康。清运门没有门磁，物理门位固定为 `UNKNOWN`；锁供电不能推定真实门扇位置，人工确认必须独立保存。
- `dev_port_runtime_state` 另保存可空 `pending_delivery_result_session_id`：凡原 session 已有设备受理或更后物理进展、没有可信 `PRE_OPEN_FAILED` 证明从未开门、但唯一最终结果尚未共同形成订单和投递后满溢检测 gate，在释放 DELIVERY 整机占位前必须把该 session 锁存到原投口。指针使用“部署 + 投口 + session”复合外键保证目标确属本投口，一投口由运行态单行天然最多一个；创建 session 前拒绝、可靠开门前失败和能够证明命令从未外调的准备失败不写该指针。只有 device 私有条件入口在锁定 port runtime 后设置/清除，普通心跳、容量读数、清运开始或后来用户扫码均不能直接清除。
- 两张运行投影都不保存笼统 `available`：下一场中心授权新作业的开门资格由部署生命周期/后台启停、精确配置应用、在线健康、端口健康、容量/满溢、袋和重量基准、用户/资金规则及整机占位实时组合；已经授权的投递 session 内本地继续不重算该组合。投口当前净重、满溢度和事件仍只由 recycling 表族拥有。
- `dev_device_occupancy` 以物理 `asset_id` 为主键，保存部署作用域、`occupancy_kind=DELIVERY/CLEAN`、投递会话或清运操作二选一的强类型外键、取得时间和锁版本。一次 DELIVERY 占位覆盖整个 session，包括全部本地继续开关门；不会因本地某轮关门而释放或创建第二个占位。session 结果期限届满或恢复路径需要释放时，若可能已发生物理内容变化但唯一订单与最终满溢 gate 尚未共同成立，必须先在同一事务设置 `pending_delivery_result_session_id`；门/旧授权仍不明确时继续保留占位或严重安全锁。
- `dev_delivery_session` 保存后端 UUIDv4 `session_uid`、作用域/部署/投口/机构用户、`PREPARED/AUTHORIZATION_QUEUED/IN_PROGRESS/RESULT_PENDING_RECOVERY/BUSINESS_CONFIRMED/PRE_OPEN_ENDED`、设备配置 `version_no + content_sha256 + mcu_payload_sha256` 与机构投递配置版本/摘要、单价、袋 ID/袋码、负余额开门阈值、人工认定克数上限、正整数负重量异常阈值（默认快照为 500 克）、30 秒本地结束等待参数、固定开始授权和中心结果恢复期限、首次设备受理/物理进展/完成时间、结束原因及锁版本。设备三元组、负重量阈值和等待时长复合引用原设备配置；单价与投口配置快照同样由复合外键锁定。会话固定原用户、部署和投口；所有授权快照创建后不可修改。
- 香橙派在 session 首次开门前本地冻结第一次稳定总重量及开门前内外照片；用户每次关门后可以在已授权 session 内本地选择继续，不向云端发送本轮开关门、重量、照片、继续意图或序号，也不重新读取中心满溢、配置、身份或钱包来授权下一次本地开门。每次关门后的本地等待最多 30 秒；用户确认结束或 30 秒无继续动作时，以最后一次关门后的可靠稳定总重量和最终关门内外照片形成唯一最终结果。硬件安全故障仍可由设备本地停止动作，但不得因此补造云端中间轮次。
- `dev_delivery_session` 是投递设备作业、唯一最终物理结果和唯一订单的幂等根。正常投递结果必须强关联既有 session；session 已结束或进入恢复态也不能改挂当前用户。无法证明原 `session_uid`、部署和授权摘要的结果进入隔离，不建立任何脱离 session 的恢复订单。
- `dev_device_command` 保存命令 UUID、作用域、稳定的强类型目标标识、命令类型、结构化关键目标、版本化语义载荷及摘要、物理执行状态和时间；投递开始/开门授权只使用 `session_uid`，不会为本地继续动作创建命令。清运使用 `clean_operation_uid`，配置应用使用 `application_uid`，满溢检测使用 `detection_uid`，空袋基准重测使用 `measurement_uid`。`CONFIRM_EDGE_EVENT/PROVIDE_PHOTO_UPLOAD_GRANT` 是 operations 的协议控制任务，不写本表。OneNet 发送租约、HTTP 结果和重试只属于 `ops_reliable_task/ops_task_attempt`；OneNet 接口返回已受理不等于设备 ACK，更不等于投递完成。
- `dev_edge_event` 是所有已认证且作用域可解析的边缘事件公共不可变头，保存全局唯一 `event_uid`、作用域/部署、严格正数 `edge_event_sequence`、事件类型、交付类别、Schema 版本、白名单目标类型/稳定键摘要、可空设备发生时间及时钟质量、后端接收时间、`payload_sha256/canonical_sha256` 和唯一来源 inbox。唯一 `(deployment_id, edge_event_sequence)` 把命令观察、物理结果、配置/故障/照片事件及确认回执放进同一部署序号空间；目标摘要只用于注册分发和诊断，实际业务关联仍由类型子表或目标领域表的强类型外键证明。
- `dev_device_command_event` 只追加开始命令的 ACK/NACK、可靠开门前失败和执行故障等命令观察；投递会话内中间开门/关门/继续动作不形成云端命令事件。它以唯一 `edge_event_id` 引用公共头，并以复合外键同时锁定原命令类型；投递开始观察必须同时强关联 session。当前 F-10 `deviceCommandObservedPayload` 只提供 `mcuCommandUid` 和符合大写符号格式、最长 64 字符的 `errorCode` 两项可空结果事实，因此本表不虚构 MCU boot/event 或 UART 版本列。`dev_physical_result` 以唯一 `edge_event_id` 和 F-10 envelope 的 `commandUid` 解析结果强关联原命令，保存设备报告的冻结配置三元组。投递/清运的首末两份称重、满溢/基准的单份称重分别无损保存 measurement UUID、状态、稳定/最后观察克数、耗时、样本数、校准版本、传感器健康、故障码和各自 MCU boot/event 身份，禁止把两次测量压成一组来源。投递仍只按整场首末重量和最终 `negativeWeightAnomaly` 结算；称重失败时稳定重量为空且禁止用 `0` 冒充。照片 URL 由 recycling 的强类型照片表拥有，可信原始报文仍只由 inbox 拥有。
- 同一 `event_uid + canonical_sha256` 返回既有处理结果；同 ID 不同摘要、同部署序号不同事件或同一 inbox 试图形成第二事件头都进入 `ops_message_quarantine` 且不得覆盖旧证据。一个公共头至多拥有一个强类型处理分支；不产生 device 子表的照片/确认控制事件仍通过该头与 inbox 保留唯一规范身份，并由注册处理器更新原强类型目标。
- 现有 OneNet/UART 临时契约缺少完整的 `session_uid/command_uid/event_uid`、香橙派部署内全局持久事件序号、整场首次/最终带符号克数、最终负重量异常布尔值及明确故障状态。这是正式 M0 联调前阻断项：目标契约必须删除旧周期身份和中间轮次上报语义；数据库和接口设计同步不代表协议、香橙派或 MCU 固件已经修改。
- I-020 进一步确认：旧 OneNet `property/set(unitPrice)`、OneNet HTTP `code=0`、串口写成功和旧 UART 单价帧都不能填写 `dev_config_application.edge_persisted_at/mcu_synced_at/applied_at`。运行投影必须保存可比较的边缘、MCU、配置及 UART 协议版本；目标 IoT/UART 证明完成前，部署不能通过首次激活。
- **2026-07-24 投递语义收口（替代 I-021～I-023 的周期化描述）**：扫码投递选项只读取实时组合投影，不写占位；开始投递由 recycling 按 D-037 一次性协调身份、当前配置、钱包和 device，原子建立 session、整机占位、会话冻结快照、开始命令及可靠任务。香橙派可靠落盘后在同一 session 内独立处理继续/结束；云端没有继续 pending、轮次处理任务、周期授权或会话内锁序。唯一 `DELIVERY_COMPLETE` 可以越过丢失的中间观察直接形成最终物理结果、订单、四个照片槽、异常和最终满溢 gate；后端结果恢复、待处理指针和迟到归并都只使用原 `session_uid`。
- **接口设计落实补充（I-026/I-027，2026-07-27 清运门修订）**：清运设备命令以 `clean_operation_uid` 为唯一业务目标，至少区分 `START_CLEAN_OPERATION`、每次恢复的唯一 `RESUME_CLEAN_OPERATION` 和操作内 MCU 重开序号；开始/恢复命令都携稳定摘要和 60 秒开始授权。香橙派必须先可靠保存操作、旧袋/基准/开门前重量、新袋预留及执行期限再 ACK。清运门没有门磁，命令只控制电磁阀；通断不能推定物理门位，门位保持 `UNKNOWN`。第一次命令进入“可能已经成功通电解锁”边界即不可普通取消，不等待真实门扇观察。`SAFE_CLOSE` 不适用于清运门。操作内重开只追加命令事件；超时或任一端重启后沿原操作进入恢复，电磁阀断电不能自动完成。正式完成物理结果唯一关联原操作，除新旧重量、新袋码和四个可空 URL 外还必须携带电磁阀已断电及清运员人工关门/完成确认事实。
- **接口设计落实补充（I-041～I-045）**：OneNet 的 `eventUid` 是 inbox 外部稳定身份，OneNet/Pulsar 消息 ID 只保存在传输元数据。可靠事件公共头、对应类型事实、业务结果、inbox `PROCESSED` 和唯一 `CONFIRM_EDGE_EVENT:<event_uid>` 意图在目标领域事务中共同收敛；确认任务稳定快照保存 `confirmation_uid + original payload sha256`，OneNet `code=0` 不结束任务，只有 `BUSINESS_CONFIRMATION_RECEIPT` 可信事件能结束。照片补授权使用 `PROVIDE_PHOTO_UPLOAD_GRANT:<grant_request_event_uid>`，临时密钥不入命令、任务快照或摘要。确认回执本身不再生成确认任务，防止递归。
- **接口设计落实补充（I-046～I-050）**：运行投影中的 MCU boot、UART 版本/能力和复位原因只表示最新快照，原命令/物理结果仍通过公共边缘事件头及类型子表保存不可变证据。当前命令观察只把 F-10 实际提供的 `mcuCommandUid` 写入强类型子表；各 measurement 实际提供的 `mcuBootId + mcuEventSequence` 则只写入物理结果对应测量。UART `txSequence` 只属于边缘诊断，不成为中心业务幂等键。MCU boot 改变、作业/门状态矛盾或配置摘要不一致时只更新故障/安全投影并沿原作业恢复，不能直接生成或改写订单。
- D-029 已确认补充：两张 runtime state 继续只保存当前健康投影；一次门、称重、MCU、相机、本地存储等故障的持续身份、首次/最近发现和真实恢复由 `dev_device_fault_event` 保存。严重安全锁仍不能被普通健康心跳自动清除，故障事件必须服从同一恢复规则。
- **接口设计恢复补充（I-030）**：安全恢复请求精确引用公开 `fault_uid` 并携带运行版本。device 事务只在没有仍执行物理动作的命令、旧命令不可执行、故障最后发现后的新鲜设备证据已恢复正常且工作人员确认现场检查时，把该活动 `SAFETY_BLOCKING` 故障推进为 `RECOVERED` 并条件解除对应锁；具备门磁的投递门还必须有可靠关闭证据，清运门则只能校验电磁阀已断电并保留“门状态为供电推定”的限制，不能使用 `SAFE_CLOSE` 或虚构门磁事实。因故障安全停住的原作业/占位可以保留并由其协调器随后收敛，本事务不结束或释放。其他故障、容量、基准、满溢、清运和投递待处理事实均不随之改写。普通健康心跳、recycling 检测或操作审计不能替代此用例。
