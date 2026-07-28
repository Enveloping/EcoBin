# EcoBin 协作与实施上下文

本文是仓库级工作入口，说明当前阶段、文档权威顺序和实施约束。它不重复保存完整业务设计；专题细节以链接的冻结文档为准。

## Agent skills

工程技能使用本地 Markdown 任务仓库和单一领域上下文：

- 任务仓库规则：[`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md)
- 状态与执行主体：[`docs/agents/triage-labels.md`](docs/agents/triage-labels.md)
- 领域文档导航：[`docs/agents/domain.md`](docs/agents/domain.md)

正式任务放在 `docs/planning/tasks/<initiative>/`。任务发布或 `status: ready` 只表示可以按依赖领取，不等于已经授权修改代码；实施仍需项目负责人明确指令。

## 1. 当前阶段

- 截至 2026-07-26，需求、P0 范围、业务模型、系统架构、目标数据库、目标接口和详细设计均已完成确认；投递已修订为一次 session 一单和设备本地继续，清运已按电磁阀解锁/人工关门的真实硬件边界修订。
- 接口设计编号为 I-001～I-055。DD-004 与 PDD-001 已分别写回 I-051～I-053；29 项正式任务已经发布到 [`docs/planning/tasks/p0-controlled-loop/`](docs/planning/tasks/p0-controlled-loop/00-index.md)。项目负责人已授权并完成 H-01、H-02、F-01～F-11、V-01、V-02；V-02 的个人主体和开发版微信限制已由项目负责人接受，设备来源归因由非阻塞的 P0-FOLLOWUP-01 延期跟踪。H-03、V-09 为 `ready` 但尚未获得实施授权。F-11 的 SQLite v4、OneNet 命令受理、固定帧 MCU 适配、照片/COS 和故障自动测试已按当前软件范围转为 `done`；真实双摄验证发现的重复索引缺陷也已修复并通过 STS 上传和 URL 下载验收。后续 OneNet 或真机验证发现范围内问题时重新打开。现有单片机无需原生实现 UART 1.0；不支持的 MCU 功能按本地保存、明确失败或未知占位降级，真机验收归 H-03。当前合计 `done` 15、`ready` 2、`in-progress` 0、`blocked` 12。
- 当前仓库已由 F-03 收口为最终九模块 reactor：OneNet/COS/微信外部实现位于 integration，旧 system 已迁入 identity，旧 business 的旧行为已分别迁入 funds、recycling、operations，并通过 device 公开端口协作。目标数据库 V1～V10 共 83 张表的独立迁移已经过 MySQL 8.4 双空库验证，但尚未接管旧应用运行库。设计文档“已冻结”和完整目标 DDL 已具备，不表示纵向业务、真实环境供应或设备协议已经整体完成。
- 近期交付重点仍是公司自用的受控 P0：用户投递、审核返现、清运换袋、机构充值和真实微信零钱提现闭环。真实资金、物理门控、租户/机构隔离和失败恢复不能因时间紧张而省略。
- P0 是近期承诺范围，M0 是 P0 通过受控真实验收后的里程碑，M1 才是公司自用正式上线准备；三者不能混用。

## 2. 开始任务前如何读取上下文

1. 先执行 `git status --short`，保留用户已有修改和未跟踪文件。
2. 本文件只负责导航。涉及跨端、IoT、硬件、旧实现差距或历史决策时，再完整阅读 [`docs/architecture/project-context.md`](docs/architecture/project-context.md)。
3. 判断“后续应实现什么”时，按以下顺序读取目标基线：
   1. [`requirements-baseline.md`](docs/planning/requirements-baseline.md)
   2. [`p0-scope-baseline.md`](docs/planning/p0-scope-baseline.md)
   3. [`business-model-baseline.md`](docs/planning/business-model-baseline.md)
   4. [`system-architecture-draft.md`](docs/planning/system-architecture-draft.md)
   5. [`database-design-draft.md`](docs/planning/database-design-draft.md)
   6. [`interface-design-draft.md`](docs/planning/interface-design-draft.md)
   7. [`detailed-design-draft.md`](docs/planning/detailed-design-draft.md)
4. 判断“系统现在如何运行”时，以当前代码、测试、根 `pom.xml`、旧运行 Flyway V1～V14、独立目标迁移 `db/p0-migration/V1～V5`、当前 OneNet 物模型和设备程序为准。
5. 目标基线与当前实现冲突并不代表文档错误：先明确是在描述现状、迁移过程还是目标，禁止用旧代码反向推翻已确认目标，也禁止把目标文档当作已运行事实。

## 3. 当前实现与冻结目标必须分开

| 范围 | 当前运行事实 | 冻结目标 |
|---|---|---|
| 后端模块 | 最终 9 模块 reactor 已完成；system/business 已退出，跨业务模块只经 `.api`，外部适配位于 integration | 9 模块物理边界已完成；后续在冻结边界内实现目标纵向业务 |
| 数据库 | 旧栈恢复单元仍是 V1～V14/13 张主要表；目标 V1～V10 的 83 表迁移只供独立 Maven 作业使用，新运行制品不含 Flyway 运行库或任何迁移脚本，并以固定 V1 marker + V10 guard 在 MySQL 8.4 Fake 空业务库通过验收，但尚未切换旧栈 | 新旧应用/数据库成对隔离；H-02 已供应正式身份和环境，后续纵向任务迁移目标业务，不导入旧业务数据 |
| Web 会话 | `localStorage` Bearer JWT，旧角色/路由 | 同源 `Secure + HttpOnly` Cookie、SPA CSRF、服务端 `jti` 会话和实时能力复核 |
| 小程序 | 旧普通用户/清运身份与接口 | 普通/清运 `aud=miniapp`；工作人员经 Web 人工绑定后用独立 `aud=miniapp-staff` 免密进入当前机构精简管理页 |
| 投递 | 旧会话、旧事件字段和当前状态拼接 | 一次有效扫码 session 一单；中间继续轮次只在设备本地，最终首末重量/四图一次上报并可靠确认 |
| 清运 | 开门建单、`cleanGross/cleanTare` 分段更新和旧 D1 UART | 可恢复 `operationUid`、解锁前稳定重量、电磁阀再次解锁、人工关门确认、单一 `cleanComplete`、原子换袋/基准/检测 gate |
| 资金 | 旧钱包余额和人工提现骨架 | 用户/机构双账本、Native 充值、双侧冻结、唯一微信转账单、回调/查单归并和渠道终态结算 |
| IoT/UART | 当前 OneNet 字段、匿名 IoT 入口、临时固定帧与旧 D1 混合 | OneNet 可信 inbox/业务确认、COS 受限直传、SQLite 恢复和规范 UART 模型；现有 MCU 只经显式固定帧适配接入 |

不要在旧投递、旧清运、旧提现或临时 UART 链路上继续做“兼容性补丁”并称其为目标实现。详细设计必须给出整体替换和受控切换顺序。

## 4. 冻结的目标系统结构

目标继续采用：

> 模块化单体 Spring Boot + 单一 MySQL 主库 + 香橙派边缘协调器 + MCU 实时控制 + OneNet/COS/微信外部适配器

目标 Maven 模块：

- `ecobin-common`：极小纯 Java 共享内核；
- `ecobin-framework`：Spring/Web/Security、可信执行上下文、租户/机构防线、MyBatis 和事务骨架；
- `ecobin-module-identity`：平台、租户、机构、工作人员、机构用户、认证和授权；
- `ecobin-module-device`：物理资产、部署、投口、配置/健康、整机占位、投递会话和设备结果；
- `ecobin-module-funds`：用户钱包、机构资金、充值、提现和微信转账业务状态；
- `ecobin-module-recycling`：投递订单、审核纠错、清运、袋、重量基准、满溢和 P0 回收查询；
- `ecobin-module-operations`：可靠 inbox/任务、审计、技术异常、对账、告警和只读运营概览；
- `ecobin-integration`：OneNet/Pulsar、COS、微信登录/支付/转账的入站与出站适配；
- `ecobin-bootstrap`：组装、配置、全局 Flyway 和跨模块集成测试，不放业务逻辑。

### 4.1 模块公开边界

- 其他模块只允许导入目标模块的 `.api` 包；公开不可变命令、查询、结果、业务 ID、必要值对象和端口。
- 不公开 Entity、Mapper、仓储实现、内部 Service、可变聚合、HTTP DTO 或第三方 SDK 类型。
- 普通公开身份、寻址、授权、幂等和跨事务恢复只使用公开 UID。DD-004 仅允许同进程、同线程、同一已开启事务的点名端口传递逐关系强类型、不可序列化 FK 构造引用；禁止裸主键、跨模块查表、传输/任务/缓存/日志泄漏和跨事务复用。
- 首次机构用户创建是唯一源聚合参与扩展：identity 定义单一同步参与端口并开启事务，funds 实现参与者创建零余额钱包；任一步失败时用户、钱包和会话整体回滚。该结构保持 `funds → identity` Maven 方向。
- 权威业务结果的所属模块开启事务，在同一线程内通过下游公开端口完成必须原子成立的结果；主链不使用异步内存事件、`REQUIRES_NEW` 或 self-invocation 切断事务。
- framework 只定义审计、可信上下文、inbox 完成和可靠动作登记等最小稳定技术端口；operations/integration 从外层提供实现，避免 Maven 循环。
- 外部能力端口由消费该能力的业务模块定义，integration 实现。第三方“已受理、处理中、终态、未知和失败”必须规范化，不能压成一个 `boolean success`。

完整规则见 [`11-module-ports-machine-contracts-i051-i055.md`](docs/planning/interface-design/11-module-ports-machine-contracts-i051-i055.md)。

### 4.2 机器契约与首版节奏

- HTTP 的机器来源是 OpenAPI 3.1；OneNet 使用强类型 JSON Schema；UART 使用单一消息注册表。实现阶段从机器来源生成或校验 DTO/模型、物模型、C/Python 编解码和人工文档。
- 首个共同可运行版本形成前，不把跨端契约 CI、完整黄金样本或自动 HIL 发布门禁设为开工前置；各端可以按实际进度分批实现并人工联调。
- 这不降低真实资金和物理安全要求：真机开关门/重启/称重、真实充值/通知/转账在投入使用前仍必须按 P0/M0 做人工受控验收。
- 共同首版形成后，再逐步加入跨语言黄金样本、Schema/生成物漂移 CI、模块包边界检查和 HIL 发布门禁。

## 5. 不得被实现阶段改写的关键边界

### 5.1 租户、机构与身份

- 一个租户是一个回收企业；机构是租户下一级部门，不支持多级组织树。
- 业务数据同时依赖 `tenant_id` 和机构作用域。租户总部可跨本租户机构，机构人员只能访问授权机构；平台跨租户必须走显式特权用例并审计。
- 不引入“平台用户跨租户成员关系”。Web 登录只使用全平台唯一登录名和密码，不输入租户编码。
- 普通用户按机构 AppID/OpenID 建立机构身份，钱包不能跨机构。首次 `wx.login` 创建用户时记录注册时间和可空来源部署，并在同一事务创建零余额钱包；后续扫码不回填来源，重复登录不补建或重建钱包。
- 租户主体账号天然全权；机构可有多名负责人。其他能力可配置且同一员工可兼任。工作人员小程序身份只能由有权限人员在 Web 人工绑定。

### 5.2 设备、投递与清运

- 物理设备资产与机构部署实例分离；调拨结束旧部署并创建新部署，历史事实仍归原机构。
- 一台设备同时只有一个作业用户/清运操作，其他人不能覆盖；门、作业或关键本地状态不明确时停止新作业。
- 开始投递由 recycling 开启复合事务，严格按 D-037 的逐节点锁序调用 identity、funds、device 参与端口；device 仍唯一拥有并写入 session、occupancy、command 和 physical fact，recycling 不访问 device 私表。继续投递属于已授权 session 内设备本地动作，不再云端授权。
- 一次有效扫码 session 最多形成一笔订单，session 单独是订单业务唯一根；event 只负责去重/冲突。中间继续开关门不上云、不上传重量/照片/过程，也不按普通满溢阻止当前用户。结束按钮或默认 30 秒选择超时后，以可空首末重量及状态、开始单价和整场四图一次上报；明确终态称重失败仍形成系统异常订单，后置满溢采样不进入完成载荷。
- 机构下发正整数克负重量检测阈值，默认 500 克；任一本地轮次下降达到阈值时只锁存 `negativeWeightAnomaly`，随最终完成载荷上报布尔值，不上报中间减少值。后端原样固化，不按整场净重补判；审核确认后才改变钱包。
- 照片缺失记录设备/网络问题但不阻止建单和返现；照片由香橙派直传 COS，后端只保存强类型槽位和可信 URL。
- 清运门只有电磁阀通断、没有门磁或关门执行器；第一次可能解锁即为不可逆边界，再次解锁沿用原操作，完成必须由清运员人工关门确认，并已有最终稳定重量或明确终态称重故障。通断不能推定物理门位，门位保持 `UNKNOWN`，`SAFE_CLOSE` 不适用于清运门。恢复中仅断电不能释放整机占位；断电且原清运员现场确认门扇关闭后可以释放整机占位，但原投口、操作和袋预留继续锁定。
- 满溢只在整场投递结束后、清运后或人工重检时采样；红外/重量模式由配置决定。任一必需来源失败都停止下一次投递 session，不能用旧值或 0 兜底。

### 5.3 审核、钱包、充值与提现

- 原始设备事实不可覆盖；审核/纠错追加认定版本。最终金额由最终重量和本单锁定单价确定，差额通过唯一钱包明细生效。
- 钱包展示待审核返现、可提现余额和提现处理中金额。待审核返现不是正式钱包资金；负余额显示在可提现余额中并禁止提现。
- 机构充值首期使用公司普通商户号 Native 扫码支付；按固定 0.6% 手续费、向上取整到分后的净额入机构本地账本。P0 禁止退款和机构额度人工调整。
- 提现创建必须同时冻结用户余额和机构额度；一名用户同时最多一笔进行中提现。审核通过后才固定唯一 `out_bill_no`，金额不能人工修改。
- 只有微信 `SUCCESS/FAIL/CANCELLED` 终态能最终结算；客服不能伪造微信终态。`NOT_ENOUGH` 表示公司公共运营账户流动性不足，只暂停全平台出款，不冻结或推翻机构本地额度。
- P0 不实现自动提现、免确认授权、充值退款、外部企业商用或平台收付通。

### 5.4 可靠性与外部边界

- 同一 MySQL 内能原子完成的事实必须同事务完成；外部调用不放进数据库事务，使用稳定业务身份、唯一可靠任务、幂等调用、通知/查单和对账收敛。
- OneNet/微信入站必须先认证、规范化并可靠写 inbox，之后才返回传输 ACK；业务事务成功与传输 ACK 是不同事实。
- 正式 IoT 上行是香橙派 → OneNet MQTT → OneNet 北向 Pulsar → 后端；下行由后端调用 OneNet 服务。生产不开放匿名设备直连通配入口。
- OneNet 传输接受、香橙派可靠受理、物理动作发生和后端业务完成是四层结果，任何一层不能冒充下一层。
- UART 1.0 使用 `0xEC42`、最大 256 字节、big-endian、CRC-16/CCITT-FALSE、HELLO、ACK/NACK、幂等命令和 MCU 启动代际事件，作为规范模型和可选 `uart-v1` 实现。当前现有 MCU 使用已确定的固定帧协议时，必须由香橙派显式 `fixed-frame` 适配且只运行一种解析器；旧 D1、自动探测、双解析和失败回退仍禁止。
- 香橙派目标 Python 3.11；不要使用开发机 Python 3.14 专属语法。`uart-v1` 重启后先
  `HELLO/QUERY_STATE` 并恢复真实状态；`fixed-frame` 没有协议级状态查询，重启后只能
  把物理状态标为未知、令本地未决工作失败并释放槽位。当前固定帧模式不增加 MCU
  作业对账或恢复锁，也绝不自动重放旧开门。
- 固定帧适配保留完整云端字段，但不等于 MCU 功能完整：配置仅保存到香橙派，缺失的
  远程控制返回 `MCU_FEATURE_NOT_SUPPORTED`，状态返回 `UNKNOWN/NOT_SAMPLED`；
  DD/EF 满溢位只作为红外观测，业务满溢仍按香橙派保存的判断标准形成。

## 6. 详细设计与实施入口

详细设计和任务拆分至少要回答：

1. 首个纵向切片的入口、应用用例、模块端口、表、可靠任务、边缘状态和验收证据；
2. 目标 9 模块的物理创建、代码搬迁和旧 `system/business` 删除顺序；
3. 新数据库 V1～V10 的 DDL/Flyway、种子数据、数据库账号和旧新应用/数据库成对切换；
4. HTTP OpenAPI、OneNet Schema、UART Registry 和各端实现的先后关系；
5. 后端、Web、小程序、香橙派和 MCU 每个任务的依赖、完成条件与手工联调点；
6. 哪些失败分支由单元/集成测试证明，哪些必须由真实 MySQL、真机或真实微信小额验收证明。

系统架构已经确认；Maven 物理模块骨架已由 F-01 完成。后续应按任务依赖迁移旧源码并
实现纵向切片。若时间不足，应缩小同一纵向切片的启用范围，不能偷偷恢复旧事实所有权或
绕过资金/安全边界。

详细设计统一入口是 [`docs/planning/detailed-design-draft.md`](docs/planning/detailed-design-draft.md)，分章给出模块/数据库、身份设备、可靠边缘、投递、清运、资金和客户端运营方案；任务依赖与领取规则见 [`08-implementation-sequence.md`](docs/planning/detailed-design/08-implementation-sequence.md)，正式任务入口见 [`p0-controlled-loop/00-index.md`](docs/planning/tasks/p0-controlled-loop/00-index.md)。任务发布或 `ready` 都不等于已授权编码。

当前真实条件：

- OneNet 已完成设备 MQTT 联通；开发环境真实 STS/COS upload/head/delete smoke 已
  通过；香橙派 `/dev/ttyS5` 可打开，现有 MCU 使用双方确定的固定帧协议；
- F-10 的软件生成物、候选 OneNet 物模型和通用 Java/Python 3.11/C11 黄金样本已完成，
  项目负责人确认无需再等待现有 MCU 工具链或原生 UART 1.0 HIL，任务已转为 `done`；
- F-11 已完成 SQLite v4、OneNet 命令可靠受理、固定帧 `AA/BB/EE` 与 `DD/EF` 适配、
  照片/COS 链路和强杀恢复测试；真实香橙派双摄、STS 上传和匿名 URL 下载已通过。
  MQTT 重连改为复用单一 Paho 网络循环。Python 3.11 硬件套件为
  `166 passed, 5 subtests passed`，契约套件为 `43 passed, 752 subtests passed`。
  香橙派当时的默认路由/DNS 波动按负责人决定暂不继续处理，不阻塞当前验收。F-11
  已按负责人接受的当前范围转为 `done`；后续验证发现范围内问题时重开，固定帧真机
  验收属于已就绪但尚未授权的 H-03；
- 微信支付和商家转账尚不能联调，因此真实充值、真实零钱到账和完整 M0 当前阻塞；
- DD-004 与修订后的 PDD-001 已确认，不再是任务 blocker；开始投递必须遵守受限 FK 引用、首次注册参与扩展、recycling 外层协调和 device 事实所有权，本地继续不得反向制造云端 cycle 或模块依赖；
- 2026-07-30 只能作为风险管理目标；初始任务量和外部条件均不支持在该日承诺完整 M0，也不能降低租户/机构隔离、资金双侧原子、门安全和失败恢复要求。

## 7. 当前工具链与常用命令

当前后端基于 Spring Boot 4.0.6、Java 21 和 Maven Wrapper。Windows PowerShell 使用：

```powershell
# 编译全部当前模块
.\mvnw.cmd compile

# 运行测试
.\mvnw.cmd test

# 修改多模块代码后，把最新模块安装到本地仓库
.\mvnw.cmd install -DskipTests

# 再启动 bootstrap，避免读取 .m2 中的旧模块 jar
.\mvnw.cmd spring-boot:run -pl ecobin-bootstrap
```

在 Bash 环境把 `.\mvnw.cmd` 替换为 `./mvnw`。

- 不要使用 `-am spring-boot:run`；它可能尝试在父 POM 查找启动类。
- 当前模块变化后，运行 bootstrap 前先 `install -DskipTests`。IDEA 编译后仍需停止并重新运行 JVM 才会加载新类。
- H2 只用于不依赖方言的轻量测试；目标迁移、外键/唯一约束、锁、事务、租户/机构隔离和资金并发以 MySQL 8.x 为权威验证层。
- Spring Boot 4 使用 Jackson 3 包 `tools.jackson.databind`；不要在新代码中误用旧 `com.fasterxml.jackson.databind`。
- 香橙派代码和工具必须兼容 Python 3.11，并使用真实 SQLite 验证断电恢复语义。

## 8. 工作区、安全与变更约束

- 工作区可能包含用户修改。尤其迁移上下文中已有 `hardware/main.py`、`hardware/pyproject.toml` 修改和未跟踪 `hardware/docs/`；不要覆盖、回退或清理这些文件。
- 不执行 `git reset --hard`、不擅自删除旧数据库/旧应用、不改真实数据库或外部平台配置，除非用户明确授权并已核对精确目标。
- `.env`、APIv3 密钥、私钥、设备 Key、AppSecret、COS/OneNet 凭证、服务器凭证和真实用户数据不得写入版本库或普通输出。
- AppSecret 的产品规则允许有权限人员在 Web 配置详情中回显当前完整值；这不允许把完整值写进日志、审计、告警或普通接口示例。
- 当前实施授权按任务范围管理：H-01、H-02、F-01～F-11、V-01、V-02 已授权并完成；
  V-02 的真实设备来源问题由非阻塞 P0-FOLLOWUP-01 延期跟踪；H-03、V-09 为
  `ready` 但未获实施授权。H-02 已完成本地开发演练和服务器阶段 0～3；
  阶段 4、正式 seed、旧库操作和真实入口切换仍须分别获得明确授权；
  用户说“讨论、计划、设计”时保持文档级工作；
  只有明确要求实施并给出范围后才修改代码和运行有副作用的迁移/外部操作。
- 文档发生阶段推进时，同步更新 `docs/README.md`、项目上下文和各基线顶部状态，避免新会话继续沿用旧阶段。

## 9. 快速导航

- [文档中心](docs/README.md)
- [项目上下文与历史差距](docs/architecture/project-context.md)
- [系统架构基线](docs/planning/system-architecture-draft.md)
- [目标数据库设计基线](docs/planning/database-design-draft.md)
- [目标接口设计基线](docs/planning/interface-design-draft.md)
- [详细设计与任务拆分](docs/planning/detailed-design-draft.md)
- [P0 受控闭环正式任务](docs/planning/tasks/p0-controlled-loop/00-index.md)
- [Agent 任务仓库配置](docs/agents/issue-tracker.md)
- [当前旧权限设计](docs/architecture/permission-design.md)
- [当前旧数据库结构](docs/architecture/database-design.md)
- [当前旧前端接口](docs/api/api-frontend.md)
- [UART 审计](hardware/docs/review/uart-protocol-audit.md)
