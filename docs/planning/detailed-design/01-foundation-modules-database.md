# 01｜Maven 模块、新数据库与成对切换基础

> 上级索引：[EcoBin P0 详细设计与任务拆分](../detailed-design-draft.md)
>
> 状态：**已批准；DD-004 已确认并写回 I-051/I-053；H-01、H-02、F-01～F-07 已完成；正式 seed 与成对切换尚未实施**
>
> 审查日期：2026-07-23
>
> 适用基线：A-006～A-010、D-001～D-046、I-051～I-056

## 1. 本章结论

P0 主链实施必须先形成两个可分别验证的基础里程碑：

1. **保持旧行为的 6→9 Maven 模块物理拆分**；
2. **在独立 MySQL 8.4 实例上，从空库安装目标 V1～V10 并启动新栈 Fake 环境**。

两者必须是连续但可归因的里程碑，不能在一个无法审查的大改中同时搬模块、替换 83 张目标表并修改投递、清运和资金行为。

本章冻结以下详细设计裁决：

| 编号 | 裁决 |
|---|---|
| DD-001 | 模块拆分阶段只改变物理边界，不增加目标业务行为；每一步必须保持旧测试和当前可观察行为。 |
| DD-002 | 独立迁移作业只扫描 `db/p0-migration`；新运行制品不包含 Flyway 运行库或任何迁移脚本，旧 `db/migration` 只随旧恢复制品保留。 |
| DD-003 | 运行应用使用 `ecobin_app` 且物理上不具备 Flyway migrate/baseline 能力；V1～V10 只由一次性迁移作业使用 `ecobin_schema_owner` 执行。 |
| DD-004（已确认） | 保留内部 `BIGINT` 复合外键；I-051 增加同进程、同线程、同事务的逐关系强类型 FK 构造引用，I-053 增加 identity 声明、funds 实现的首次机构用户原子创建参与扩展。2026-07-24 因投递业务改为 session 一单删除云端 cycle 表；已执行 V1～V10 为 83 张历史表，V20 以清运修改留痕表替换废止审核表后目标仍为 83 表。这不是 DD-004 的键策略变化。 |
| DD-005 | `PREPARED → QUIESCING → QUIESCED → ACTIVATED` 按“应用 + 数据库 + 所有真实入口”成对切换；越过真实入口闩锁后禁止直接重启旧栈。 |

## 2. 当前实现证据与退出条件

### 2.1 实施前差距快照

截至 2026-07-23，实施开始前的代码仍是 6 个 Maven 模块：

```text
ecobin-common
ecobin-framework
ecobin-module-system
ecobin-module-device
ecobin-module-business
ecobin-bootstrap
```

关键证据如下：

- `ecobin-common` 仍依赖 MyBatis-Plus，并保存 `BaseEntity`、`PlatformBaseEntity` 和业务枚举；
- `ecobin-framework` 仍直接保存 OneNet、COS 和微信实现及其 SDK；
- `ecobin-module-business` 直接导入身份 `UserMapper`、设备 Entity/Mapper/Service；
- `ecobin-module-device` 和 `ecobin-module-system` 直接调用 framework 中的外部平台实现；
- 当前没有任何目标 `.api` 包；
- bootstrap 使用全项目通配 `@MapperScan("org.enveloping.ecobin.**.mapper")`；
- 生产代码只有 5 个显式 `@Transactional`，尚不存在目标锁序和可靠任务事务；
- 当前数据库仍是旧 V1～V14，应用启动自动迁移、允许 baseline/自动建库，默认使用 root，并输出 MyBatis SQL；
- 当前测试关闭 Flyway 并以 H2 `schema-h2.sql` 为主要数据库结构；
- 当前没有独立目标实例、V1～V10、数据库身份供应、epoch guard 或成对切换设施。

这些事实说明目标能力不能通过在旧 `business` 和旧表上继续加字段、Controller 或状态值完成。
本节保留为实施前审计证据，不代表 2026-07-24 的当前 reactor。

### 2.2 截至 2026-07-26 的实施进展

- F-01 已建立九个目标模块，F-02 已把旧 system 行为迁入 identity 并使 system 退出；
  F-03 已把旧 business 行为迁入 funds、recycling、operations，并收口为最终 9 子模块
  reactor。
- 首次注册同事务参与端口、不可伪造 FK 构造引用和可信会话上下文已经实施；F-03 又建立
  device/funds/recycling 的迁移期公开端口和模块边界门禁，JDK 21 下共 82 项 Java
  测试通过。
- F-04/F-05 已在独立 `db/p0-migration` 实现 V1～V5 共 54 张表，并在 MySQL 8.4.10 的
  两个全新空库通过严格安装、结构一致性、组织隔离、约束反例和重装拒绝验证。
- F-06 已完成 V6～V10，目标纪元达到 83 张领域表和 71 行环境无关权限目录；
  MySQL 8.4.10 双空库结构与约束矩阵已经验证。
- F-07 已从运行制品移除 Flyway 运行库和目标/旧纪元迁移脚本，并关闭 SQL init 和自动建库；
  固定 V1 description/script/checksum 且要求完整 V1～V10 的只读 guard 进入 readiness。
  正确 V10 空业务库以 `ecobin_app` 就绪，错误纪元严格失败，Fake OneNet/COS/微信不装配
  真实客户端且阻断真实入口/凭证。
- H-02 已完成正式数据库身份与目标环境供应；旧应用和旧 V1～V14 运行库尚未执行成对
  切换，后续业务用例、正式 seed 和 H-06 切换仍按独立任务推进。

### 2.3 基础里程碑完成定义

只有同时满足以下条件，后续纵向业务切片才可以在目标结构上实施：

- 根 POM 只声明 9 个目标模块，旧 `system`、`business` 不再存在；
- 每个模块只导入允许的上游模块，跨业务模块只导入 `.api`；
- OneNet、COS、微信 SDK 及协议实现只位于 `ecobin-integration`；
- 每个业务模块显式配置自己的 Mapper 扫描；bootstrap 不再通配扫描；
- 新应用构建产物不包含旧迁移目录；
- 两个全新 MySQL 8.4 空库均可独立、重复安装 V1～V10，并得到相同结构；
- `ecobin_app` 能完成允许的业务 DML，但不能 DDL、删除事实或修改受保护不可变列；
- 空库、旧纪元、错误 V1、失败迁移或低于 V10 时，新应用拒绝就绪；
- 正式 seed 用例可以幂等创建试点组织结构，但不能伪造重量基准、余额或业务历史；
- Fake 外部适配器启动时不会接收真实 OneNet、COS、微信入口或调用真实设备/资金渠道。

## 3. 九模块物理施工图

### 3.1 POM 依赖

依赖方向固定如下，箭头表示“左侧依赖右侧”：

```text
framework  → common
identity   → framework
device     → framework + identity
funds      → framework + identity
recycling  → framework + identity + device + funds
operations → framework + identity + device + funds + recycling
integration→ framework + identity + device + funds + recycling + operations
bootstrap  → 全部模块
```

模块要求：

| 模块 | 详细设计要求 |
|---|---|
| `ecobin-common` | 纯 Java、小型、无 Spring/MyBatis/外部 SDK；只保留真正跨域稳定的值类型、错误契约和通用响应。 |
| `ecobin-framework` | Spring/Web/Security/MyBatis/事务、可信执行上下文和稳定技术端口；不得保存 OneNet、COS、微信业务适配器。 |
| `ecobin-module-identity` | 租户、机构、小程序、人员、用户、认证、会话、能力与授权。 |
| `ecobin-module-device` | 资产、部署、投口、配置、运行状态、占位、投递 session、设备命令与物理证据。 |
| `ecobin-module-funds` | 用户钱包、机构账户、充值、提现、微信支付/转账业务状态和不可变资金明细。 |
| `ecobin-module-recycling` | 投递订单及其审核纠错、清运记录及修改留痕、袋、重量基准、满溢和 P0 回收查询。 |
| `ecobin-module-operations` | inbox、可靠任务、尝试、审计、隔离、告警、对账和受控恢复。 |
| `ecobin-integration` | OneNet/Pulsar、COS、微信登录/支付/转账及入站通知的协议适配。 |
| `ecobin-bootstrap` | 应用组装、环境配置、epoch guard、全局迁移位置声明、seed runner 和跨模块集成测试。 |

根 POM 只做依赖和插件版本管理。Lombok 不再由根 POM 无条件注入全部子模块；模块确实需要时自行声明，任何公开 API 不依赖 Lombok 生成的可变语义。

### 3.2 模块内目录

业务模块统一采用：

```text
org.enveloping.ecobin.<module>
├─ api/
│  ├─ command/
│  ├─ query/
│  ├─ result/
│  ├─ id/
│  ├─ value/
│  └─ port/
├─ application/
├─ domain/
├─ infrastructure/
│  ├─ persistence/
│  └─ config/
└─ web/
```

`integration` 使用：

```text
org.enveloping.ecobin.integration
├─ onenet/inbound
├─ onenet/outbound
├─ cos
└─ wechat/
   ├─ login
   ├─ payment
   ├─ transfer
   └─ notification
```

`bootstrap` 只允许：

```text
assembly/
config/
database/epoch/
seed/
```

以及跨模块集成测试。业务 Controller、Mapper、领域状态机或渠道协议不得放入 bootstrap。

### 3.3 公开端口与配置

每个模块显式导出自己的 Spring 配置类，并只扫描本模块 Mapper。其他模块不得导入：

- Entity、Mapper、Mapper XML 或 Repository 实现；
- application/domain/infrastructure/web 包；
- 第三方 SDK 请求、响应或异常类型；
- 裸数据库主键或任意实体通用的内部主键容器；DD-004 仅允许专用 `.api.persistence` 类型在已点名的当前事务同步参与端口中构造复合 FK。

公开业务端口按“完整业务能力”定义，不能拆成让调用方自由排列锁顺序的 `lockX/updateY` 接口。例如：

- recycling 公开“审核或纠错并同步钱包差额”，而不是让 Controller 分别调用“更新订单”和“写钱包”；
- funds 公开“双侧冻结并创建提现”，而不是分别暴露“扣用户余额”和“扣机构余额”；
- device 公开“取得整机占位并建立投递 session”，而不是让调用方自行操作占位表。

### 3.4 跨模块外键身份构造（DD-004，已确认）

独立复核发现 I-051 的普通公开身份规则与 D-001～D-035 的内部 `BIGINT` 复合外键之间存在施工冲突。项目负责人已经确认采用原方案 A，并将两个窄例外正式写回 I-051/I-053：

1. **同事务 FK 构造引用**：事实所有模块在同一进程、同一线程和一个已经开启的数据库事务内，创建按主体或关系强类型的引用；只允许已点名的同步参与端口使用它把必要键分量写入接收模块自有表的复合 FK。
2. **首次机构用户原子创建参与扩展**：identity 在自己的 `.api.port` 声明唯一 `OrganizationUserRegistrationParticipant`，funds 依赖 identity 并提供空钱包初始化实现；bootstrap 只负责装配。

以下边界随 DD-004 一并冻结：

- HTTP、OneNet、UART、微信、MQ、可靠任务、缓存、日志、审计、异常和普通跨事务 Java 命令仍只允许公开稳定身份；
- 公开 UID/业务号继续负责寻址、授权、幂等、审计目标和恢复，FK 构造引用不得参与这些判断；
- 引用不得是裸 `Long`、任意 `Map` 或可承载任何表主键的通用容器，不实现 Java `Serializable`，`toString()` 不输出内部键值；
- 引用对象不得持久化、跨线程、跨事务或跨进程保存；当前事务只能把其中键分量写入接收模块拥有表的 FK 列；
- 接收模块不得借此查询其他模块 Mapper、Entity、Repository 或私表；
- 初版以专用包、明确调用点、代码审查和测试限制范围，自动 ArchUnit/CI 门禁按 I-055 后续加入；
- 内部 `BIGINT`、复合作用域外键、V1～V10、表写所有权和既有 Maven DAG 均不改变；
  2026-07-24 业务修订单独把目标表数由 84 调整为 83。

未采用的方案 B 是把全部跨模块关系改为公开 UUID/业务号候选键及复合外键。该方案需要重写 D-001～D-035、V1～V10 和相关索引/锁序，当前不实施，也不得在个别表中混用。

首次注册扩展只能用于首次机构用户创建：identity 开启事务并写用户/注册归因后调用单一参与者，funds 加入原事务创建唯一空钱包，随后 identity 创建会话；任一步失败则整体回滚。重复 `wx.login` 不再次调用，不自动补建钱包。参与者不得按 `List` 注入、动态注册或推广为通用事件总线/`afterCreate` 插件链。这样保持 `funds → identity`，不增加 identity 对 funds 的 Maven 反向依赖。

## 4. 模块搬迁执行序列

每一步单独提交、全量编译并运行当前测试：

### M1：记录旧行为基线并建立空骨架

- 记录当前 `./mvnw test` 结果、模块依赖、Controller 清单和配置入口；
- 新建 9 个目标 POM 和包骨架；
- 建立显式模块配置与 Mapper 扫描；
- 此时不移动业务行为、不接新库。

### M2：先提取 integration

- 将 framework 中 OneNet、COS、微信代码移入 integration；
- 将 business 中 OneNet 消费/分发入口移入 integration；
- 业务模块定义出站端口，integration 实现；
- 入站消费者和出站客户端必须是不同 Bean。

### M3：system 迁为 identity

- 按目标包结构搬迁当前身份行为；
- 建立最小身份/组织/作用域公开端口；
- 只保持旧行为，不在本步顺带开发完整新机构权限模型。

### M4：提取 funds

- 把 Wallet、Withdraw 和相关测试搬到 funds；
- 用窄 legacy 身份端口替代直接 `UserMapper` 引用；
- 审核通过仍不得在本步伪造成真实微信成功；旧行为只用于基线比较，目标资金链随后整体替换。

### M5：拆分 device 与 recycling

- 资产、部署、投口、投递会话、设备命令归 device；本地继续轮次不进入中心数据库；
- 投递订单及其审核、清运记录及直接修改留痕、袋、满溢和 P0 查询归 recycling；
- 删除对 device Entity/Mapper 的直接导入，以公开端口连接。

### M6：建立 operations 并收紧底层

- 收拢现有审计/统计基础，建立可靠技术端口的实现位置；
- 把业务枚举和 MyBatis 基类移出 common；
- 删除旧 `business`，完成 bootstrap 显式组装；
- 运行 `./mvnw install -DskipTests` 后再运行 bootstrap 集成测试，避免读取 `.m2` 旧模块 jar。

上述 legacy 端口只用于模块搬迁阶段维持旧行为。新数据库纵向切片落地后必须删除，不能成为长期共享旧表的接口。

## 5. 同事务参与端口

模块依赖方向不能靠异步事件绕开强一致要求。采用“上游定义端口、下游实现、bootstrap 装配”的同步参与方式：

| 用例 | 事务发起者 | 同步参与者 |
|---|---|---|
| 首次 `wx.login` 创建机构用户与空钱包 | identity | identity 定义唯一同步注册参与扩展，funds 使用当次强类型 FK 构造引用初始化空钱包 |
| 投递审核/纠错与钱包差额 | recycling | funds 钱包差额端口 |
| 人工钱包调整与投递停投闸恢复 | recycling | funds 人工调整端口 |
| 投递/清运完成与业务确认任务 | recycling | device 物理证据端口、operations 可靠任务技术端口 |
| 提现创建与双侧冻结 | funds | identity 作用域校验、operations 可靠任务/审计技术端口 |

端口实现必须以 `Propagation.REQUIRED` 加入外层事务，不使用：

- 内存领域事件；
- `REQUIRES_NEW`；
- `@Async`；
- self-invocation 形成的隐式失效事务；
- 提交后补写钱包、确认任务或审计事实。

特别规则：

1. 首次注册由 identity 发起事务；参与命令同时携带公开机构用户 UID、创建钱包所需受信事实和 DD-004 限定的当次强类型 FK 构造引用。钱包初始化失败则用户、钱包和会话整体回滚，任何实现不得退回裸主键、跨模块查表或提交后补建。
2. 人工调账由 recycling 外层用例先锁当前投递配置 head，取得受信阈值，再调用 funds；funds 不反向依赖或读取 recycling。
3. 同一业务模块的普通写端口不得接收外部传入的“已经锁过”布尔值；锁上下文由强类型参与命令表达并在实现中再次校验。

## 6. 目标 V1～V10

新迁移目录固定为：

```text
ecobin-bootstrap/src/main/resources/db/p0-migration/
```

首个迁移纪元：

| 版本 | 文件名 | 表/职责 |
|---|---|---|
| V1 | `V1__p0_epoch_and_iam_core.sql` | IAM 核心 8 表：平台、租户、机构、小程序、工作人员、任职、权限、授权。 |
| V2 | `V2__device_inventory_and_configuration.sql` | 设备资产/部署/当前部署、投口、配置/快照/应用及运行投影 9 表。 |
| V3 | `V3__organization_users_and_sessions.sql` | 机构用户、用户能力、工作人员小程序绑定和三类会话 6 表。 |
| V4 | `V4__device_operations_and_evidence.sql` | 故障、占位、投递 session、命令、公共边缘事件、命令事件、物理结果 7 表。 |
| V5 | `V5__recycling.sql` | 投递、修订、照片、清运、袋、基准、容量、满溢共 24 表。 |
| V6 | `V6__funds.sql` | 配置、钱包/机构双账本、充值、提现、商户绑定、渠道单及闸门共 20 表。 |
| V7 | `V7__operations.sql` | inbox、隔离、可靠任务/尝试、审计、告警、对账运行/问题/动作 9 表。 |
| V8 | `V8__cross_module_constraints.sql` | 只补此前不能建立的循环及跨模块外键。 |
| V9 | `V9__immutability_guards.sql` | 只建立 AppID 激活和机构用户注册归因两类不可变触发器。 |
| V10 | `V10__permission_reference_data.sql` | 只插入权限目录和环境无关静态参考。 |

已执行 V1～V10 合计 83 张历史表；V20 删除废止的 `rec_clean_revision` 并新增 `rec_clean_record_change` 后，目标仍为 83 张表。实现者必须从数据库基线逐表生成“表→迁移→所有者→主键/公开键→复合外键→唯一/CHECK→索引→DML 身份”矩阵；本章表数不是替代逐表基线的简表。

迁移脚本禁止：

- `IF NOT EXISTS`；
- `FOREIGN_KEY_CHECKS=0`；
- baseline 污染库；
- 把租户、账号、密码、设备、袋、配置、余额或秘密写入 Flyway；
- 修改已经在任何持久环境成功应用的迁移。

全新空库任一迁移中途失败时保存证据并丢弃该无业务事实的半成品库，从新空库重装；越过真实入口闩锁后只允许前向修复。

## 7. 数据库身份、配置与验证

五类身份固定为：

| 身份 | 运行位置 | 权限边界 |
|---|---|---|
| 实例初始化/恢复管理员 | 环境供应/灾难恢复 | 建库、账号、初始授权；不进入应用或日常迁移。 |
| `ecobin_schema_owner` | 一次性迁移作业 | 目标 schema DDL 和必要授权；不进入后端容器。 |
| `ecobin_trigger_definer` | V9 前供应 | 禁止交互登录，仅保留触发器执行所需最小权限。 |
| `ecobin_app` | 后端运行与正式 seed | 严格列级 DML、只读 Flyway history；无 DDL/GRANT/TRIGGER/旧库权限。 |
| `ecobin_backup` | 备份任务 | 只读及备份工具必要的元数据/锁权限。 |

目标环境统一：

- MySQL 8.4 LTS，锁定完整镜像版本和 digest；
- UTC、严格 SQL mode、业务写默认 `READ COMMITTED`；
- 关闭 `createDatabaseIfNotExist`、`baseline-on-migrate` 和应用启动 migrate；
- 关闭生产 SQL 明文和业务 DEBUG；
- 应用启动只读验证固定 V1 description/checksum 且版本不低于 V10。

测试至少包含：

- 两个空库从零安装结果一致；
- 错误纪元、空库、旧 V1～V14、失败迁移和低版本均被 guard 拒绝；
- `ecobin_app` 正常业务 DML 成功；
- `ecobin_app` 执行 DDL、修改不可变列、删除事实、操作旧库均失败；
- V9 两类更新触发器的正反例；
- 所有 Mapper、复合外键、锁序和并发测试使用真实 MySQL 8.4；
- H2 只保留给不涉及 MySQL 方言、锁和约束的快速测试。

## 8. 渐进式试点 seed

seed 是显式命令，不是 Flyway 数据，也不获得 schema owner 权限。它只能编排**已经实现的正式应用用例**，不能为了提前启动新栈直接写表或在基础任务里偷做后续业务。

试点数据随纵向切片逐步形成：

| 数据 | 由哪个切片的正式用例建立 |
|---|---|
| 一个试点租户、两个机构、租户主体和必要工作人员、小程序配置 | 身份与组织切片 |
| 投递配置、设备资产、部署和投口 | 设备配置切片 |
| 初始袋、投口绑定及清运配置 | 清运切片 |
| 机构零余额账户、提现配置和必要计数器 | 资金切片 |

基础阶段只交付 epoch guard、空目标库启动和禁用真实外联的 Fake bootstrap，不声称已有完整试点 seed。所有相关纵向用例完成后，bootstrap 才可提供一个“完整试点 seed”编排入口供 H-06 使用。

每个分步 seed 使用稳定输入键：不存在时通过正式用例创建；存在且声明字段相同则 no-op；同键字段冲突立即失败。重跑不能重置密码。

初始重量基准必须来自袋安装后的可信实际称重。未取得基准时保留投口阻断状态，禁止 seed 固定 `0` 伪造。

机构用户和用户钱包不由 seed 批量创建。用户首次 `wx.login` 时在一个业务事务中建立机构用户和零余额钱包。

## 9. 成对切换与回退

### 9.1 恢复单元

旧栈恢复单元必须一起保存：

- Git commit/tag 和可运行制品；
- 旧配置模板和旧 V1～V14；
- 逻辑 dump、校验和、Flyway history、行数清单；
- 一次隔离环境整对恢复证据；
- 旧应用、数据库实例/容器、账号和卷的精确清单。

用户已明确旧业务数据无需迁入目标库，但该前提必须在最终激活前再次确认。

### 9.2 状态

```text
PREPARED
  → QUIESCING（受控过程）
  → QUIESCED
  → ACTIVATED
```

- `PREPARED`：新 MySQL、新应用、迁移、权限、seed 和 Fake 验收完成；所有真实入口与凭证硬阻断。
- `QUIESCING`：停止旧 HTTP、OneNet 消费、worker 和下行，收敛事务、门、作业、MQ 与可能开始的外部调用。
- `QUIESCED`：最终 dump 与恢复验证完成，旧库停止写入并离线/只读保留。
- `ACTIVATED`：按同一张所有权清单依次切换 Nginx/API、OneNet 消费、任务 worker、设备下行和真实渠道凭证。

任何时刻只能有一套所有者。只切 HTTP 而让旧 worker、旧 OneNet 消费或旧渠道凭证继续工作不算完成切换。

### 9.3 闩锁

出现任一情况即越过不可直接回退闩锁：

- 新栈接收任一真实用户、设备、OneNet 或微信入口；
- 新 worker 获得真实外调能力；
- 新栈注入真实渠道凭证并可能外调；
- 发布与旧栈不兼容的小程序、香橙派或 MCU 协议/配置。

闩锁前可以整对恢复旧应用和旧库。闩锁后发生问题时必须停止入口、保护新库并修复前进，或设计显式事实回迁；不得因为“上线时间短”直接重启旧栈。

## 10. 本章设计追踪项

本表只用于追踪本章覆盖范围；可领取任务已经统一映射到
[`p0-controlled-loop`](../tasks/p0-controlled-loop/00-index.md)，正式编号、依赖和状态以独立任务文件为准。

| 草案 ID | 标题 | 类型 | 依赖 | 独立验收结果 |
|---|---|---|---|---|
| FND-01 | 旧栈恢复基线与所有权盘点 | HITL | 无 | 旧应用+旧库在隔离环境整对恢复，实例/卷/入口清单完整。 |
| FND-02 | 九模块边界搬迁 | AFK | 当前构建基线 | 9 POM、显式 MapperScan、跨模块仅 `.api`、旧行为测试通过。 |
| FND-03 | 目标 MySQL 8.4 与 V1～V10 | AFK + 主审 | 冻结数据库基线 | 两个空库安装一致，83 表及约束矩阵通过，失败半库不能启动。 |
| FND-04 | 数据库身份、GRANT 与 epoch guard | HITL | FND-03 | 正向 DML 与权限负测通过，运行容器无 owner 凭证，启动不迁移。 |
| FND-05 | inbox 与可靠任务 tracer | AFK | FND-02、FND-03 | 收件/任务原子、租约接管、崩溃重投和业务回滚均通过真实 MySQL 测试。 |
| FND-06 | epoch guard 与空目标库 Fake bootstrap | AFK | FND-03、FND-04 | 正确纪元可启动、错误纪元拒绝、真实外联硬阻断；不直接写业务表。 |
| FND-06A | 渐进式试点 seed 编排（正式图映射 F-12） | AFK + HITL 输入 | 对应身份/设备/清运/资金纵向切片 | 只调用已完成正式用例；分步可重跑、冲突失败，缺真实基准保持阻断。 |
| FND-07 | 成对切换和闩锁前回退演练 | HITL | 所有基础和纵向切片 | 一次性环境完成切换与整对回退，保存全入口所有权证据。 |

## 11. 主审否决项

以下实现不得通过本章审查：

- 只改目录名，仍允许 business/system 内部实现或 Mapper 被跨模块导入；
- 让 framework 继续成为 OneNet/COS/微信大杂烩；
- 在模块搬迁同时改写全部目标业务，导致旧行为基线不可比较；
- 新应用继续发现旧 V1～V14 或在启动时自动迁移；
- 目标库使用旧实例中的另一个 schema 代替物理隔离；
- 以 root 或 schema owner 运行后端；
- 通过 `IF NOT EXISTS`、baseline、关闭外键或 Flyway repair 掩盖半成品结构；
- 为避免公开端口而跨模块查表、引用 Mapper/Entity，或在业务 API 中返回裸内部主键；
- seed 伪造 0 重量基准、余额、订单或资金历史；
- 只切 Nginx，不切 OneNet 消费、worker、设备下行和真实渠道；
- 越过真实入口闩锁后直接重启旧应用/旧数据库。
