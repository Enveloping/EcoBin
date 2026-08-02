# EcoBin 项目上下文（Claude Code → Codex）

> 整理日期：2026-07-11。来源为项目 `CLAUDE.md`、`~/.claude/projects/C--D-004-Project-002-Java-EcoBin/memory/`、近期 Claude Code 会话/计划、仓库文档与 Git 状态。
> 本文记录对话中形成、仅靠代码不容易恢复的决策。代码和更新日期更晚的专题文档若与本文冲突，以较新的事实为准。

> [!IMPORTANT]
> 2026-08-02：满溢准入已切换到 V25“设备被动上报、只有当前袋明确 `FULL` 才阻止下一次投递”。后端不再为投递/清运创建检测 gate 或主动下发 `SAMPLE_FULLNESS`；无上报、失败或旧袋结果不新增阻断，新袋默认 `NOT_FULL`。投递选项与开始接口使用同一当前袋 `FULL` 事实。完整裁决见 [`fullness-reporting-v25.md`](fullness-reporting-v25.md)，本文及旧规划中相反描述均由该裁决覆盖。

> [!IMPORTANT]
> 2026-07-28：需求、P0 范围、业务模型、系统架构、数据库设计、接口设计和详细设计修订已确认。H-01、H-02、F-01～F-11、V-01、V-02 已完成；V-02 的个人主体和开发版微信限制已由项目负责人接受，设备来源归因由非阻塞 P0-FOLLOWUP-01 延期跟踪。H-03、V-09 为 `ready` 但尚未授权。F-11 的 SQLite v4、OneNet 命令受理、固定帧 MCU 适配、照片/COS 和故障自动测试已按负责人接受的当前范围转为 `done`。后续真实双摄验证发现数字索引会重复选择 DECXIN，现已改用稳定 `by-id` 路径并通过真实 STS 上传和 URL 下载验收；项目负责人决定暂不继续处理香橙派当时的默认路由/DNS 波动。项目负责人确认现有 MCU 使用协商后的固定帧协议，香橙派保留云端契约；对 MCU 不支持的能力，按逐项决策采用香橙派本地保存、fixed-frame 正常兼容投影或明确拒绝/失败，不增加物理命令重发、MCU 作业重启恢复或双事实安全锁；真实线路和执行器验收归 H-03。2026-07-29 又确认当前 OneNet 控制台已经是 9 服务 / 13 事件版本，并以“先跑起来”为首要目标：冻结 MCU 无法提供的状态允许由 fixed-frame 兼容层构造；`applyConfiguration` 只要由香橙派可靠保存并设为活动配置即可返回成功和 `APPLIED`，不要求真实下发 MCU。逐项决策持续记录在 [`onenet-edge-handling-decisions.md`](../../hardware/docs/review/onenet-edge-handling-decisions.md)。其他任务仍须逐项授权，`ready` 只表示依赖允许领取。正式上游依次为
> [`requirements-baseline.md`](../planning/requirements-baseline.md)、
> [`p0-scope-baseline.md`](../planning/p0-scope-baseline.md) 和
> [`business-model-baseline.md`](../planning/business-model-baseline.md)，冻结的系统结构见
> [`system-architecture-draft.md`](../planning/system-architecture-draft.md)，冻结的目标数据库见
> [`database-design-draft.md`](../planning/database-design-draft.md)，冻结的目标接口见
> [`interface-design-draft.md`](../planning/interface-design-draft.md)，施工入口见
> [`detailed-design-draft.md`](../planning/detailed-design-draft.md)，正式任务入口为
> [`tasks/p0-controlled-loop/00-index.md`](../planning/tasks/p0-controlled-loop/00-index.md)；截至 2026-07-23，I-001～I-055 均已确认。I-051～I-053 已包含同事务 FK 构造引用、首次注册同步参与扩展以及 recycling 复合投递协调/device 事实所有权的窄边界；I-055 明确首版前不强制跨端契约 CI、完整自动契约门禁或自动 HIL 发布门禁，但真实资金和真机人工验收不变。本文下方若描述旧实现或历史决策，不能替代这些新基线。

## 1. 项目与组件

EcoBin 是智慧环保回收箱系统：Spring Boot 4.0.6 + Java 21 的 Maven 多模块后端，配套 Web 管理后台、微信小程序和香橙派设备程序。

- `ecobin-common`：极小纯 Java 共享内核，只保留响应、异常和通用角色值。
- `ecobin-framework`：Security/JWT、多租户和通用基础设施；F-01 后不再保存外部平台实现。
- `ecobin-module-identity`：F-02 已承接原 system 的管理员、租户、用户和认证行为，并建立可信执行上下文与公开身份边界。
- `ecobin-module-device`：设备、投口、设备会话，并通过迁移期公开端口提供设备查询与统计。
- `ecobin-module-funds`：F-03 已承接旧钱包、提现行为，并保留 F-02 首次注册事务参与端口。
- `ecobin-module-recycling`：F-03 已承接旧投递审核、清运和袋行为。
- `ecobin-module-operations`：F-03 已承接旧统计聚合，并只通过其他模块公开端口读取数据。
- `ecobin-integration`：F-01 后承接 OneNet/Pulsar、COS 和微信适配实现。
- `ecobin-bootstrap`：依赖组装、配置、Flyway、启动入口。
- `frontend/web`：React 18 + TypeScript + Vite + Ant Design/ProComponents。
- `frontend/miniprogram`：原生 TypeScript 微信小程序 + TDesign。
- `hardware`：香橙派 Python 设备侧；目标运行时 Python 3.11，1 GB 内存，不运行 Chromium。

后端结构、命令和通用约定见根目录 `CLAUDE.md`。

当前已是 common、framework、identity、device、funds、recycling、operations、
integration、bootstrap 共 9 个模块的终态物理 reactor；原 `system` 已在 F-02 退出，
原 `business` 已在 F-03 搬迁并退出，跨业务模块只导入 `.api`。完整依赖和事务规则见
[`system-architecture-draft.md`](../planning/system-architecture-draft.md) 与
[`I-051～I-055`](../planning/interface-design/11-module-ports-machine-contracts-i051-i055.md)。

DD-004 保留内部 `BIGINT` 复合外键，只允许点名同步端口在同线程同事务传递不可序列化
的关系专用 FK 构造引用；首次机构用户由 identity 事务同步调用 funds 参与者创建零余额
钱包。修订后的 PDD-001 只规定“开始投递”由 recycling 按 D-037 协调，device 仍唯一
拥有和写入 session 等设备作业事实；继续投递属于已授权 session 内设备本地动作。

## 2. 不应反复推翻的业务决策

### 身份与租户

- 不引入全局“平台用户 → 多租户成员关系”模型。
- B 端：平台管理员创建租户，只创建 `sys_tenant`。
- C 端当前实现：回收箱二维码包含 `tenant_id`，市民扫码后通过微信登录注册到该租户；旧角色还允许把普通用户提升为清运员或设备管理员。
- C 端目标设计：允许直接进入小程序或扫描设备二维码注册；首次 `wx.login` 成功创建机构用户记录即写入独立注册时间和可空的来源设备部署实例，用于按设备和时间统计地推成果，尚未绑定手机号的用户也计入注册。直接注册的来源为空，后续扫码不回填；手机号绑定时间另存，归因不影响权限、钱包或投递资格，具体字段以数据库设计草案 D-013 为准。
- 所有业务表带 `tenant_id`；普通业务请求由登录上下文和租户拦截器隔离，平台域账号按既定规则放行。
- 完整权限模型见 `docs/architecture/permission-design.md`。

### 投递

- 当前模型是“设备上传后建单”，不是用户开门时预建订单。
- 小程序开门只激活 `biz_device_session` 并下发开门；设备完成称重后发 `deliveryComplete`，后端再创建每袋独立订单。
- 当前旧实现的归属取设备活跃用户会话。无会话或会话过期时仍建无主单（`user_id=null`），不返现；会话被后来的用户覆盖时接受“最近用户”语义。该规则仅描述待替换现状，不是 2026-07-24 目标设计。
- 当前旧实现使用 OneNet 消息 ID（缺失时回退 MQ message ID）作为幂等键，历史列
  `delivery_token` 承载该键；目标使用后端 `sessionUid`、香橙派持久 `eventUid`、
  规范摘要和部署内全局事件序号。不要从旧字段名反推目标语义。
- 2026-07-24 目标投递改为一次有效扫码 session 最多一单：session 单独是订单业务唯一根，
  `eventUid` 只负责可靠事件去重/冲突。可空首末重量及状态、开始单价和整场四图只在唯一
  `DELIVERY_COMPLETE` 中上报；明确终态称重失败仍形成系统异常订单，后置满溢采样不在
  完成载荷内，中间继续轮次不上传。任一本地轮次重量减少达到后端下发阈值（默认 500 克）
  时，只随最终载荷上报 `negativeWeightAnomaly` 布尔标志；后端原样固化，不按整场净重补判。
- 照片由设备决定 COS 对象 key；投递或清运完成事件先回传四个槽位的当时状态，仍在拍摄或上传的槽位 `url=null`。实际上传完成后再由 `photoStatusReported: AVAILABLE` 回传可信 URL，后端不自行拼 URL。

### 清运：现状与目标不要混淆

后端的目标正常清运切片已经落地：清运员准备操作后，后端冻结旧袋/基准、预留新袋并下发 `START_CLEAN_OPERATION`；可信 `CLEAN_COMPLETE` 到达后在同一事务创建清运记录、交换袋关系、处理新基准、建立清运后检测 gate 并提交确认意图。清运员本人查询、Web 查询以及 `clean.edit` 直接修改和只追加变更历史也已实现。当前未闭合的是香橙派/MCU 的目标状态机、再次解锁和超时恢复等跨端真机路径；设备侧旧 D1 毛重/皮重链仍须退出，不能与后端目标事实混用。

2026-07-23 已正式确认 [`I-026～I-030`](../planning/interface-design/06-cleaning-bags-fullness-recovery-i026-i030.md)：

1. 清运员扫描部署二维码、选择投口并扫描换入袋；后端创建可恢复 `operationUid`、冻结旧袋/旧基准、预留新袋并取得整机占位，不提前创建清运记录，也不使用最近心跳重量冒充开门前重量。
2. 香橙派必须先把完整操作上下文和期限可靠写入 SQLite，再从 MCU 取得并保存本次真实开门前稳定总重量，二者都成功且开始授权仍有效才允许 MCU 开门。MCU 负责屏幕/按钮、门控和稳定称重；香橙派负责操作身份、基准、照片、结果计算、COS 和 OneNet。
3. 清运门没有门磁或关门执行器，MCU 只给电磁阀通电解锁且门自动弹开；第一次解锁可能
   已执行即为不可逆边界。以后只能在原操作内再次解锁、由清运员人工关门确认完成，或
   超时进入原清运员恢复。电磁阀通断不能推定物理门位，门位保持 `UNKNOWN`，
   `SAFE_CLOSE` 不适用于清运门。恢复中
   只有“断电 + 原清运员现场确认门扇关闭”才可释放整机占位，原投口、操作和袋预留仍锁定。
4. 最终安全关门并由清运员确认后，香橙派可靠保存单一 `cleanComplete`；后端一次事务创建清运记录、交换袋关系、建立/清空新基准、把新袋容量初始化为 `NOT_FULL` 并结束操作。香橙派随后可以按 V25 规则上报新袋状态变化，后端不主动发起采样。
5. 袋没有生命周期状态；无效新皮重仍只能由确认当前袋为空后的真实重测恢复。满溢准入只认当前袋设备上报的 `FULL/NOT_FULL`；投递结果待处理和严重安全锁继续通过各自目标接口规定的现场确认边界恢复。
6. 清运员是可信工作人员；清运完成事务直接建立已完成记录，不进入审核、认定、通过或驳回流程，也不产生钱包、积分、返现或其他奖励。有 `clean.edit` 权限的后台人员可以直接修改允许修改的清运业务数据，保存后立即生效并追加变更留痕；设备原始证据以及袋关系、重量基准、容量 gate 和设备安全状态仍只能由对应真实业务事实推进。

旧家目录计划 `~/.claude/plans/tender-hopping-moth.md` 和其中 `G1` 示例只保留历史参考价值；若与当前接口、数据库设计或 UART 审计冲突，以仓库内较新的设计为准。OneNet/COS 和边缘 SQLite 语义现已由 I-041～I-045 冻结；正式实施仍须生成机器物模型、完成 UART 契约及跨端测试，不能直接把旧示例代码视为已验证实现。

### 钱包、充值与提现：现状与目标不要混淆

- 当前代码只有旧钱包和人工提现骨架，真实微信 Native 充值、机构双账本及微信商家转账尚未接入，不能把旧的“审核通过即完成”继续扩展为目标流程。
- 2026-07-23 已正式确认 [`I-031～I-035`](../planning/interface-design/07-funds-recharge-withdrawal-wechat-i031-i035.md)：P0 所有机构共享 EcoBin 公司普通商户号，但各机构 AppID 必须分别与该商户绑定；机构充值使用 Native 支付，用户提现使用 APIv3 商家转账的用户确认收款模式。
- 充值先建立本地单和固定下单意图，微信成功事实与机构净额入账分为两个可恢复事务。提现创建先冻结用户和机构两侧资金，审核通过后才建立唯一 `out_bill_no`；只有微信 `SUCCESS/FAIL/CANCELLED` 终态可以结算。
- `NOT_ENOUGH` 表示公司公共运营账户流动性不足，只暂停系统商户出款闸门；机构本地额度继续可信。P0 不实现自动提现、免确认授权、充值退款、机构额度人工调整或更换微信原单重试。

### 运营治理与最小概览

- 2026-07-23 已正式确认 [`I-036～I-040`](../planning/interface-design/08-operations-audit-alert-reconciliation-statistics-i036-i040.md)：平台技术页只能查看并恢复原可靠任务、确认隔离项，不能直接重放消息或修改业务终态。
- 操作审计、技术尝试、聚合告警、对账问题和业务事实保持分层；工作人员确认告警或标记对账问题已处理都不等于来源已经恢复。
- M0 每日资金对账只做 EcoBin 内部逐交易收敛和可证明的完整遗漏补齐，不宣称已经接入微信官方账单文件；复杂财务归档仍是 M1。
- 最小运营概览按北京时间日期范围查询，并把期间流量与当前快照分开。operations 通过各业务模块公开查询端口，在仅供该只读概览使用的 `REPEATABLE READ` 事务中从同一快照组装；业务写入仍使用 `READ COMMITTED`，且不保存可人工修改的累计统计。

## 3. IoT、OneNet 与照片链路

- 正式上行：设备 MQTT → OneNet → 北向 Pulsar MQ → `OneNetMqConsumer` 解密 → `OneNetEventDispatcher` 分发。
- 正式下行：后端调用 OneNet 物模型服务 API。设备连接 token 与平台下行 API token 是两套凭证、版本和资源路径，不能混用。
- P0 后端只配置一个 OneNet 产品 ID，可信设备名固定等于硬件 SN；资产表不重复保存该可推导映射。设备 Key 只配置在对应香橙派，后端下行使用产品级 AccessKey。
- 整条链路只需要出站连接：上行订阅 MQ、下行调用 OneNet HTTP、设备访问 COS；没有 OneNet HTTP 公网入站回调需求。
- 2026-07-23 已正式确认 [`I-041～I-045`](../planning/interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)：可靠边缘事实使用稳定 `eventUid`、原作业身份、规范摘要和部署内全局 `edgeEventSequence`；OneNet 传输 ACK、设备受理、物理结果和后端业务确认严格分层。后端权威事务与确认意图共同提交，设备持久化确认并回执后才清理原事件。
- 2026-08-02 已把“在线”拆为两层事实：资产级 `oneNetConnectionStatus` 表示 OneNet 传输连接状态，由北向 `deviceOnline` / `deviceOffline` 通知、已鉴权设备消息和 OneNet 下行结果更新；部署级 `edgeConnectionStatus` 表示业务有效在线，必须同时有 OneNet 在线事实和未过期的可信运行快照。默认运行快照间隔 30 秒，连续 3 个周期未收到即视为业务离线。接口必须同时返回两层状态及其观测时间，不能再用单个布尔值混用。
- 业务命令只在 OneNet 明确 `ONLINE` 且运行快照新鲜时下发；`UNKNOWN` 仅允许首次配置等接入探测。OneNet `10421` 表示目标离线，任务保持 `PENDING`、标记 `DEVICE_OFFLINE`，不增加连续失败计数，等上线通知或可信设备消息自动唤醒；`10410` 表示当前产品下找不到设备，任务阻断为 `DEVICE_IDENTITY_UNRESOLVED`，不能盲目重试。后端不再用周期性服务调用探测在线，Pulsar 生命周期通知是主要唤醒来源。
- OneNet 返回 HTTP 200 但业务码非零时，只将明确的临时平台内部错误 `10500` 归为可重试；`10415` 等参数、物模型、权限及其他未进入临时白名单的业务错误直接永久失败，不能让冻结载荷持续重放。
- 当前联调产品为 `tB6NlBWW0V`，唯一应保留的设备名/硬件 SN 为 `test-divice-1`。历史假设备及其关联事实应使用受控清理工具处理，不能只删资产主表；操作手册见 [`fake-device-cleanup.md`](../operations/fake-device-cleanup.md)。
- 2026-07-23 已正式确认 [`I-046～I-050`](../planning/interface-design/10-uart-protocol-i046-i050.md)：UART 1.0 使用 `0xEC42`、最大 256 字节、big-endian 和 CRC-16/CCITT-FALSE 的有界二进制帧；启动先 HELLO/QUERY_STATE，命令 ACK 与物理结果分层，关键 MCU 事件提交边缘 SQLite 后才 ACK。`txSequence`、`mcuCommandUid`、`mcuBootId + mcuEventSequence` 和云端作业身份互不替代，任一端重启都禁止自动重放旧开门。
- 投递和清运照片统一为设备直传 COS。对象 key 由设备在 `ecobin/{deploymentCode}/{workType}/{workUid}/` 授权前缀内生成；临时凭证只在发送时附加且不进入稳定摘要/日志。完成事件回传四个槽位状态，尚未上传完成的槽位 URL 为空；上传完成后再由照片状态事件回传可信 URL。
- Jackson 使用 Spring Boot 4 的 Jackson 3 包 `tools.jackson.databind`；不要在 framework 模块误用 `com.fasterxml.jackson.databind`。
- 当前物模型与消息结构见 `docs/iot/onenet-thing-model.md` 和 `docs/iot/onenet-thing-model.json`，但它们是待替换的运行现状，不是目标契约。实施 I-041～I-045 时必须同步修改代码、JSON、Markdown 与 OneNet 控制台模型，禁止保留旧写协议作为生产兼容层。
- I-019/I-020 的目标契约要求配置以版本和摘要分别证明“香橙派已可靠落盘”和“必要 MCU 项已同步”。当前 fixed-frame 阶段采用 2026-07-29 确认的运行优先例外：`applyConfiguration` 可靠保存并成为香橙派活动配置后即可返回成功及 `APPLIED`，允许构造兼容 `mcuCommandUid`；这不表示 MCU 已真实接收配置。具体边界见 [`onenet-edge-handling-decisions.md`](../../hardware/docs/review/onenet-edge-handling-decisions.md)。

## 4. 硬件侧当前上下文

- 香橙派是云侧/业务侧小电脑：OneNet MQTT、COS 上传、流程编排；MCU 连接屏幕、传感器和执行器，通过 UART 与香橙派通信。
- 物理 UART 通常至少连接交叉的 TX/RX 和共地 GND。F-11 已把 `hardware/main.py`
  的正式入口切向 SQLite 新骨架，当前 schema 已推进到 v6；当前现有单片机使用双方
  确定的 `AA/BB/EE` 下行与
  `DD/EF` 上行固定帧适配器。规范 `uart-v1` 只保留为未来明确选择的可选实现，不是
  当前 MCU 的通信路径，也不是 F-11 完成门。
- 固定帧兼容保留香橙派—OneNet—后端完整契约，但 MCU 实际能力不完整：配置只在香橙派
  保存，缺失的远程控制明确返回不支持，状态返回 `UNKNOWN/NOT_SAMPLED`；DD/EF 满溢位
  只作为红外原始观测，业务满溢仍按香橙派保存的规则判断。
- 固定帧只有单投口且无 ACK、CRC、流程编号和状态查询。香橙派不重发物理启动命令，
  重启后不恢复或重放 MCU 作业，只把本地未决工作置失败并释放槽位；当前不增加
  UART v1 快照完整性锁、SQLite/MCU 作业冲突锁或部署身份启动锁。
- 运行时必须显式选择 `fixed-frame` 或 `uart-v1`，不自动探测或失败回退；固定帧适配
  只存在于香橙派硬件边界，不改变 OneNet、后端或业务事件结构。旧 D1 和旧 gross/tare
  清运路径仍须退出。
- 固定帧实施记录和能力降级矩阵见
  `hardware/docs/review/fixed-frame-mcu-adapter-2026-07-27.md`；真实线路、屏幕和执行器
  行为仍由 H-03 验收，软件测试不得冒充真机能力。
- 当前 OneNet 9 服务 / 13 事件的逐项处理结论见
  `hardware/docs/review/onenet-edge-handling-decisions.md`；后续讨论结果统一增量写入该文档。
- 2026-07-11 迁移记忆时工作区已有用户修改：`hardware/main.py`、`hardware/pyproject.toml`，以及未跟踪的 `hardware/docs/`。这些不是 Codex 创建的，必须保留。

## 5. 已知工程坑

- 2026-08-02 的 V25 投递联调已经打通真实 OneNet/COS、模拟 MCU/双摄、订单、业务确认
  回执和当前袋 `FULL` 准入。期间遇到的 systemd 双实例、旧边缘数据、V24→V25 迁移、
  `.m2` 旧 jar、OneNet 确认引用缺少 `PORT_FULLNESS_STATE`、BLOCKED 原任务受控恢复和
  时钟偏差等问题，已整理为
  [`投递全链路联调复盘与复跑手册`](../operations/delivery-e2e-integration-retrospective-2026-08-02.md)。
  下次跨端联调先按该手册执行环境与契约预检，不以 OneNet `code=0` 或出现订单单独宣称
  闭环完成。
- `./mvnw spring-boot:run -pl ecobin-bootstrap` 不会重建其他模块，会直接使用 `.m2` 的旧 jar。改过 identity/framework/device/business 或其他模块后先 `./mvnw install -DskipTests`，再运行 bootstrap。不要用 `-am spring-boot:run`，它会尝试在父 POM 找主类。
- IDEA 运行使用各模块 `target/classes`，代码编译后仍需 Stop/Run 重启 JVM 才能替换已加载类。
- `AdminController.list` 与 `TenantController.list` 返回 `Result<List<T>>`，Web 端做客户端分页；用户、设备、投递、清运、提现等主要列表返回 `PageResult`，由服务端分页。
- 微信 `jscode2session` 返回 JSON 内容但可能标为 `text/plain`，要先取字符串再用 Jackson 手工解析。
- 小程序 TDesign 曾完全无样式，根因是 `ignoreDevUnusedFiles=true` 丢弃 npm 组件，加上 `es6=false/enhance=false` 不转译 ESM；不是 `style:v2`。修复后需重新构建 npm、清缓存并重启开发者工具。
- 本地数据库与渠道密钥统一保存在 Git 忽略的
  `.ecobin/application-local-secrets.yml`；IDEA、开发脚本和根 Compose 共用该文件，
  Fake/Real 只切 profile。容器内数据库地址仍由 `docker-compose.yml` 的 backend
  environment 覆盖为 `mysql` 服务名；生产继续使用 `/run/secrets`。

## 6. 产品与协作偏好

- 用户明确说“目前先计划，不改代码”时严格停留在计划阶段。
- Web 后台视觉改版曾被明确搁置，等用户与导师确定方向；目前优先保证功能与跨端契约。

## 7. 资料导航与权威顺序

1. 当前代码、测试和 Flyway 迁移：运行事实。
2. `docs/iot/onenet-thing-model.md`、`docs/architecture/database-design.md`、`docs/api/api-frontend.md`、`docs/architecture/permission-design.md`：专题设计。
3. `docs/planning/requirements-baseline.md` → `p0-scope-baseline.md` → `business-model-baseline.md` → `system-architecture-draft.md` → `database-design-draft.md` → `interface-design-draft.md` → `detailed-design-draft.md` → `tasks/p0-controlled-loop/00-index.md`：判断目标实现、施工顺序和任务状态的正式上游链。
4. `hardware/docs/review/uart-protocol-audit.md`：最近 UART 现状审查。
5. 本文：跨会话决策、旧新差距和工作方式。
6. `~/.claude/projects/C--D-004-Project-002-Java-EcoBin/*.jsonl`：只有在上述资料无法回答时才回溯的原始会话证据。

## 8. 建议的续作入口

DD-004、修订后的 PDD-001、29 项任务粒度/依赖、`status/executor` 分类和
2026-07-30 仅作风险排序均已确认。29 项任务已发布并同步 2026-07-24 修订到
[`p0-controlled-loop/00-index.md`](../planning/tasks/p0-controlled-loop/00-index.md)：H-01、
F-01、F-02、F-03、F-04、F-05、F-06、F-07、F-09、H-02 已完成；H-02 已通过本地
MySQL 8.4.10 开发演练和服务器整改阶段 0～3 验收，项目负责人接受当前试验期的
`.ecobin` ACL 受限明文保管例外，加密密码库及其异机密文副本延期到下一版本；
F-10 的机器来源、通用三语言黄金样本和适配责任边界已完成，任务已转为 `done`；
F-08 已完成 Fake 可靠任务 tracer、MySQL 8.4
验收和两项 P1 复审；F-11 已按当前软件范围转为 `done`；
V-01 已完成身份/Web 纵切、六项 P1 复审修复和真实 MySQL 验收并转为 `done`；
V-02 已按项目负责人接受的受限微信环境边界转为 `done`，设备来源归因保留在非阻塞
P0-FOLLOWUP-01；H-03 的 F-11 依赖已经解除并转为 `ready`，V-09 的 V-02/F-08
依赖也已解除并转为 `ready`，两项均未获实施授权。
当前共 `done` 15、`ready` 2、`in-progress` 0、`blocked` 12。任务依赖解除没有自动
授予实施权限。

实施入口已经明确：

- 保持旧行为的最终 9 模块物理边界已经完成，后续目标业务必须在该边界内通过公开
  `.api` 端口实现，不能恢复旧 system/business 大模块；
- V1～V21 已完成前向迁移和 MySQL 权威验证；V19 已增加投递配置管理，V20 已删除清运审核列/约束并以清运修改留痕表替换历史审核修订表，V21 已补齐无门磁正常清运事实；F-07 又完成固定 V1 marker、
  V10 epoch/readiness guard、最小 `ecobin_app` 空业务库启动和 Fake 外联硬阻断，
  并在两项 P1 补强后通过复审；H-02 本地演练及服务器整改阶段 0～3 技术门已通过，
  服务器目标数据库身份、加密备份和隔离恢复均已供应并验证；
- F-09 HTTP/OpenAPI 客户端传输已完成复审并由项目负责人确认；
- V-01 身份/Web 纵切已完成复审并获准合入；V-02 已按项目负责人接受的个人主体和
  开发版环境边界转为 `done`，真实设备来源归因由非阻塞 P0-FOLLOWUP-01 跟踪；
  V-09 已解除任务依赖成为 `ready`，但尚未获得实施授权；
- F-11 的固定帧适配、照片/COS、MQTT 重连、强杀/COS 自动诊断、契约生成边界和能力
  降级文档已按当前范围完成；真实双摄已按 DECXIN 外部、icspring 内部完成拍照、上传
  和 URL 下载校验。H-03 后续在同一适配版本上执行固定帧线路、屏幕状态机和物理行为
  真机验收，发现 F-11 范围内问题时重开；
- 后续按投递及其审核与钱包、清运、满溢恢复、充值、提现和 operations 的纵向切片推进；
- MCU 固件、数据库环境、真实微信、真机验收和成对切换分别保持 HITL；
- 完整跨端契约 CI 和自动 HIL 发布门禁按 I-055 留到共同首版形成后的升级阶段，但人工门安全、断电恢复、真实资金和真机验收不能延期。

当前 OneNet MQTT 已联通，开发环境 STS/COS upload/head/delete smoke 和真实香橙派
双摄上传/匿名下载已通过；固定帧 MCU 适配已合入，但不支持的 MCU 功能仍按“本地保存/
明确失败/未知占位”降级。Python 3.11 硬件套件当前为 `178 passed, 5 subtests passed`，
契约套件为 `43 passed, 752 subtests passed`。香橙派当时的默认路由/DNS 波动按
项目负责人决定暂不处理且不阻塞 F-11；微信支付/商家转账仍不可联调。真实条件或
软件链路缺失时只能标记相应软件阶段，不能宣称 M0。
