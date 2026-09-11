# MCU—香橙派契约对齐与分批实施计划

> 日期：2026-09-12；状态：计划已整理，待评审和明确实施授权。
> 本轮只修改文档，没有修改机器契约、生成代码、运行程序或设备；没有执行烧录、部署或真机测试。
> 业务依据：[逐项共识](../architecture/mcu-edge-minimal-facts-consensus-2026-09-11.md)。
> 分工背景：[总体方案](../architecture/mcu-edge-refactor-plan-2026-09-11.md)。
> 本文细化总体方案的契约差异、文件、依赖、验证和切换顺序；不新增一套协议来源，
> 不发布正式任务编号，也不把“计划完成”或测试清单写成“能力已经实现”。

## 1. 实施目标与不变边界

完成一套能由 MCU 提供真实事实、香橙派保存和自动恢复、后端按结果处理的共同版本。
正常投递与清运保持现有业务入口；不让普通通信中断或重启普遍变成人工解锁。

- MCU 启动先停止输出，启动编号归零，由香橙派保存并分配非零编号；不新增 MCU Flash 持久化。
- MCU 明确回复受理/拒绝。丢回复先查询原业务，不重复发送可能造成再次动作的启动命令。
- 完成结果冻结；香橙派写入本地数据库成功并确认后，MCU 才能清理。
- 投递门采用最近有效方向：PB6/PB7 的 01=关、10=开；真实输出另外记录，不声称测得门位。
- PB5 只暂停关门，释放后继续仍有效的关门动作；开门忽略 PB5。PB4 未接，PB5 不是到位证明。
- 称重按 250 ms 目标间隔、最近 5 点全跨度不超过 100 g 取均值、最多 5 s 纯波动取中位数。
  准确度交人工；真实缺值不变成零；中位数不冒充稳定、不仅因此增加审核或停机。
- MCU 重启且投递必要数据确实丢失：问题记录，不进入订单结算/余额/自动提现；
  香橙派再发一个新的关门动作，核对恢复条件后自动接单。已保存完整结果不作废。
- 清运丢失旧重量：保留原清运操作，由原清运员完成换袋、关门确认，核对新袋并取得真实新皮重；
  旧数据仍缺失，不能用新皮重填回旧重量。新袋与基准由后端去重、原子应用。
- 非重启的真正最终称重失败，与重启丢数据分开：仍保留系统异常订单，人工审核前不产生资金效果。
- 保留设备身份、照片、业务数据、永久动作账本、权限和更新互斥；不以清库、刷 TF 卡代替迁移。

### 对总体方案的一处源码纠正

当前 `hardware/main.py::_make_uart_link` 创建串口链路，业务进程独占正常 UART；
`job_safety.py` 通过本地接口向永久更新器申请许可和登记动作，`updater_agent.py` /
`updater_store.py` 管理永久账本。`communication_agent.py` 是云端通信代理，不是串口代发器。
这也符合既有[永久层设计](../architecture/orangepi-business-runtime-release-and-update-design.md)。

因此本次保留“业务进程单一串口所有者 + 永久更新器许可/账本 + 更新时交接”的边界。
消除的是固定帧兼容层合成启动编号、受理、稳定性等行为，不是删除正常分层，也不额外迁移 UART 所有权。

## 2. 契约差异表：复用、修订和新增

`contracts/` 继续是字段、枚举、编号、单位、摘要和校验的唯一机器来源。
下面的消息名中，注明“拟新增”的仅是职责名称，尚未分配线上编号。

| 项目 | 现有契约/实现 | 计划处理及跨端影响 |
|---|---|---|
| 帧与编码 | Registry 已有 EC42、big-endian、CRC、256 字节帧、有界缓冲 | 复用；按最大合法载荷重新预算，超限采用已有集合机制或明确分段，不截断结果 |
| 协议版本 | 规范 UART 1.0 / Registry 1.0.0-rc.3；现用固定帧另有自己的 v2 命名 | 先做兼容性判定，再定主/次版本；不因开始重构就先命名“v2”。不兼容语义不得冒充旧 1.0 |
| 未分配启动身份 | HELLO/ACK 和关键事件的启动编号现在均要求非零 | 引导状态允许 MCU 未分配为 0；正常业务事件仍必须非零。新增绑定职责，不能把所有事件约束改成允许 0 |
| 受理与拒绝 | 已有 ACK/NACK，但引用传输序号；旧固定帧只有本地下发状态 | 明确绑定原命令身份/摘要和真实受理结果；传输确认、受理、输出和完成分别表达 |
| 业务启动 | 已有 START_DELIVERY_SESSION、AUTHORIZE_DELIVERY_FIRST_OPEN、START_CLEAN_OPERATION | 优先复用职责；保留首重、照片保存和首次开门授权之间的已有边界，START 不直接冒充开门完成 |
| 原业务查询 | QUERY_STATE 和三段 STATE_SNAPSHOT 已存在 | 扩充原启动/业务/命令查询及一致快照；明确进行中、结果可取、查不到。查询不清结果、不释放占用 |
| 去重 | 已有 UUID、内容摘要、事件序号 | 补目标 MCU 启动身份、缓存淘汰后拒旧规则及序号溢出行为；不让旧命令因缓存删除再次执行 |
| 结果交接 | 已有关键事件保留及 SQLite ACK 语义 | 复用事件和集合身份，补完整结果摘要/保存确认；分片全部验证落盘后才确认完整结果交接 |
| 防夹与门事实 | MCU checklist 的方向与当前源码/最新共识相反 | 修正 checklist、Registry 语义与固件；分别报告有效目标、动作是否仍有效、实际 PB6/PB7、PB5 状态 |
| 采样参数 | 已有 window、fluctuation、sampleCount、timeout 等字段 | 复用 100 g、5、5000 ms；补轮询周期的单一配置来源。全机/投口两个 timeout 出现处和配置摘要同步核对 |
| 取值方式 | WeightValueKind 只有稳定/末四次/可用样本均值等 | 拟增加独立重量超时中位数类型；不借用距离测量的 MEASURED_MEDIAN。C/Python/Java/OneNet 同步 |
| 波动与故障 | UNSTABLE 目前绑定旧均值及 WEIGHT_UNSTABLE | 保留真实波动状态，明确“中位数可用”和“当前传感器故障”不同；后端不能仅按 faultCode 非空排除中位数 |
| 恢复关门/清运 | 已有 SAFE_CLOSE、RESUME_CLEAN_OPERATION、UNLOCK_CLEAN_DOOR | 复用动作职责并修订恢复前置；恢复清运上下文本身不自动开锁，清运门不套用 SAFE_CLOSE |
| 自动投递问题记录 | 已有手动 recovery quarantine 事件和服务 | 增加独立自动数据丢失结果，复用合适的证据存储；不能冒充操作者调用手动隔离或降低其已部署门槛 |
| 清运缺旧重量结果 | 正常完成校验仍要求旧重量稳定 | 用明确恢复结果形状表达旧重量缺失、新袋事实和新皮重；不放宽成任意缺字段的正常结果 |
| 状态/升级/验收 | 固定帧提供 F0/F1、F2/F3、URL 等；原生 Registry 未必职责齐全 | 建完整能力对照，保留烟感、满溢、二维码、配置、自检、升级互斥等现有能力；缺项须显式报不支持 |

权威修改入口：`contracts/uart/uart-registry.yaml`、`uart-registry.schema.json`、
`mcu-review-checklist.md`；OneNet 的 `common.schema.json`、`events/events.schema.json`、
`commands/commands.schema.json`、包络及 `thing-model.mapping.yaml`；必要的 HTTP 修改进入
`contracts/http/openapi.yaml`。生成器、校验器和 `contracts/examples/` 一起更新。

生成物一律生成，不手改 `hardware/uart_protocol.py`、`hardware/onenet_projection_model.json`、
`contracts/**/generated/` 或 Web 生成类型。候选物模型生成不等于 OneNet 已导入。

## 3. 进入编码前收敛的工程设计

### 3.1 启动绑定与旧帧隔离：首个必须通过的验证点

Pi 分配、MCU RAM 归零的方向不变。先列出握手状态、接收条件和通道缓冲假设，再验证：

1. MCU 在查询前、查询回复后、接受绑定前后分别重启；Pi 在候选编号落盘前后退出。
2. 丢失绑定回复时只查询，不重新灌入旧编号；同一轮连续读到 0 不制造多次重启记录。
3. 旧查询、旧绑定、旧状态和旧保存确认延迟到新启动；尤其验证旧“查询 + 绑定”整对滞留。
4. 原 MCU 没重启而 Pi 重启：恢复原编号、原命令序列和原业务，不重新分配。

新查询编号只能关联回复，单独不能证明 MCU 未在绑定前再次重启。
候选方案应明确停止发送、排空/废弃缓冲及建立新会话的可验证边界；不能只写“清串口后安全”。
如果不依赖 MCU 持久化、可靠启动区分量或有界通道条件就无法成立，记录反例和最小缺口，
再向用户说明取舍，不能擅自改成 MCU 随机正式启动编号、Flash 计数或假设接好了 NRST。
这项未通过不冻结启动绑定协议，也不放行自动判定 MCU 重启的恢复路径；其他独立逻辑可先验证。

### 3.2 命令去重和结果保存：实施建议

保留 UUID 和摘要。建议 MCU 每个已绑定启动周期维护有序命令的“已处理最大序号”，
Pi 在 SQLite 先保存序号/意图，并对有副作用命令保持单个在途。只读查询使用独立查询身份。
近期同编号同内容返回原受理结果；同编号不同内容拒绝；结果正文被清理后的旧序号仍拒绝执行，
只能报告详细结果已不可取，不能说从未执行。未来序号缺口、拒绝是否消耗序号、位宽和溢出
均在第一批用状态序列定稿，禁止序号回绕成新命令。

结果按原启动/业务/结果身份及摘要冻结。完整结果可以由现有阶段事件组成，不要求强塞入一帧。
接收分片不代表可清全部结果；Pi 在一个本地事务中保存完整结果和待上报记录，再发保存确认。
MCU 收到确认只交出这份结果，不代替 Pi 的占用检查或服务器对袋/基准的应用确认。

### 3.3 称重细节：建议参数，实施前通过测试固定

已接受的 250 ms / 5 点 / 100 g / 5 s 不再重复讨论。其余建议如下，不假称现场已经验证：

- 周期以请求起始时刻调度；上一请求未完成不叠加，错过周期不突发补发。每个合法完整响应
  生成样本编号和接收时间；相同数值的新响应可计数，重复读取缓存不可计数。
- 继续使用单请求读取；响应期限先以现有 200 ms 为待验证初值，测实际响应分布后固定。
  校验地址、功能码、字节数量、CRC、符号、范围；超时后的迟到帧不能冒充下一请求响应。
- 稳定窗口只用本阶段最近 5 个新合法原始读数，不先平滑。建议窗口最大跨度 1500 ms，
  最后读数距判定不超过 750 ms；这里是防旧值，不要求额外等满 1500 ms。
- 超时中位数建议至少 5 个本阶段合法读数，且末次仍满足上述新鲜度；仅波动时必定采用。
  故障后的合法响应可恢复采样，不能因一次坏包就把整次测量判死；持续断线和孤立旧值不兜底。
- 中位数覆盖本阶段全部合法样本；偶数取中间两项平均。均值/中位数统一四舍五入到整数克，
  正负恰好半克均向远离零方向取整，用宽整数累加，避免 C/Python/Java 默认取整不同。
- 5 s 是本次测量的硬截止，不在超时后再加一轮采样等待。机械等待独立于采样定时，
  不以 PB5 认定门到位；界面需区分机械等待与测量耗时，不能暗中恢复旧 10 s 测量。
- 配置版本包含这些参数及摘要；全机与投口重复参数不得冲突。坏配置明确拒绝应用，
  不默默执行另一套值。支持范围先按现有能力约束，不借此改变硬件量程或自动校准。

这些建议用于区分“纯波动”和“数据已经断了”，不是提高重量准确度验收标准。
若真机不能按目标周期产生响应，报告实测差异，不自动退回 600 ms 并声称已满足 250 ms。

## 4. 分批实施、文件与完成条件

下列 P0～P7 仅是本文批次标签，不是正式任务编号。每批保留独立代码差异和验证记录；
实施顺序不等于部署顺序，不要求分批烧录。建议先准备可一起联调的 MCU/Pi 候选包再手动烧录。

| 批次 | 内容 | 依赖 | 完成标志 |
|---|---|---|---|
| P0 | 契约逐消息设计、兼容性判定、启动/去重证明 | 明确实施范围 | 无歧义字段表、状态轨迹、反例、帧/内存预算；未决项不假冻结 |
| P1 | 修改契约、生成物与执行校验 | P0 对应项目通过 | 合法样例三端一致，非法 payload 被拒绝，生成无漂移 |
| P2 | MCU 时间、门控、称重核心拆分 | 第 1 节已确认规则 | 纯 C 测试及目标工具链构建通过，不驱动真机 |
| P3 | MCU—Pi 正常业务与结果交接 | P1、P2 | 模拟串口正常投递/清运、真实 SQLite 保存确认闭环 |
| P4 | 双端重启恢复与永久账本 | P3、启动绑定验证通过 | 不重放旧机械动作，异常/恢复都有独立持久证据 |
| P5 | 后端三类结果与清运恢复事务 | P1；联调依赖 P3/P4 | MySQL 结果幂等、资金隔离、袋/基准原子应用通过 |
| P6 | HMI、热点验收与必要业务展示 | P1～P5 对应接口；HMI 交互确认 | 不同按钮含义分开，数据和取值方法可见，不绕过验收 |
| P7 | 成对发布、单台 HIL 与回退演练 | P0～P6 发布必需项通过 | 现场证据完整，再申请正式接单切换 |

### P0 / P1：契约落地

修改第 2 节所列机器来源，以及 `contracts/tools/generate_contracts.py`、
`contracts/tools/validate_contracts.py`、`contracts/tests/test_contracts.py` 和相关样例。
补一份逐消息能力矩阵：复用/修改/新增/明确不支持、载荷长度、身份关联、动作前置、重试规则。
版本根据破坏性变化确定；旧 1.0 不能静默承载不同方向或不同启动语义。
把已接受的新规则同步到相关接口/详细设计基线的称重、重启和门事实章节，注明替代关系；
只修订本次已确认范围，避免机器契约已更新而上游仍要求旧稳定均值或一律重启中止。

`contracts/DEFERRED-HARDENING.md` 记载生成 C/Java 并非完整 payload 防线，且有超长粘帧等历史缺口。
逐项复核当前实现，只补本次原生运行所需缺口，不把“已有生成器”当成生产校验齐全。
必须覆盖重算 CRC 后仍非法的 enum/长度/保留位/摘要，以及超过 512 字节的连续合法帧增量排空。
协议状态不合法即使帧合法也拒绝；不能只检 CRC。

### P2：MCU 可独立验证的核心

修改 `hardware_mcu/USER/main.c`、`stm32f10x_it.c`、`usart1.c/.h`、
`mcu_runtime_logic.h` 和 `STM32-DEMO.uvprojx`。
拟在同一 `USER/` 下增加小型 `.c/.h` 模块：`runtime_clock`、`scale_reader`、
`weight_measurement`、`door_control`、`clean_lock`；避免一次重写底层库或引入 RTOS。

先拆持续时钟，再替换采样。清运锁改独立截止时刻，保留明确配置的通电时长，
不能把旧“8 次采样”在 250 ms 下继续使用；烟感时钟也不随采样变化。
门控使用目标有效位与动作有效位：重启无目标、实际 00；取消/更新后的 PB5 释放不能复活旧动作。
保护与串口解析不得被 5 s 称重等待阻塞。

扩展 `hardware_mcu/tests/test_mcu_runtime_logic.c`、
`hardware/tests/test_mcu_runtime_logic_c.py`，拟新增时间/称重状态测试。
覆盖计时回绕、锁时长、PB5 暂停/释放/改开、阶段隔离、100/101 g 边界、零/负数、奇偶中位数。
Keil 工程明确纳入新文件，记录链接后的 Flash/RAM、栈及队列容量；通用 C 测试不能替代目标构建。

### P3：正常业务纵向闭环

MCU 拟增加 `USER/edge_protocol.c/.h`、`work_runtime.c/.h`、`hmi_controller.c/.h`，
接入生成的 `USER/uar/` 编解码；调整 `usart3.c` 等现有接线调用点。
单一业务槽保存真实阶段、首次/末次测量、原结果；暂不含重新设计显示布局。

Pi 修改 `hardware/uart_link.py`、`main.py`、`edge_store.py`、`work_manager.py`、
`command_processor.py`；按职责提取拟新增 `mcu_session.py`、`measurement_policy.py`，
不把所有新增逻辑继续堆入主工作管理器。
明确关闭机械启动的通用自动重发；旧 `fixed_frame_mcu_adapter.py` 不再为新模式生成事实，
只留旧制品/明确旧模式作为切换前基线，不在同一连接并行解析。

验证 `hardware/tests/test_uart_link_v1.py`、`test_uart_protocol.py`、`test_edge_store.py`、
`test_work_manager.py`，新增结果交接轨迹测试。真实 SQLite 在“写前、提交后 ACK 前、ACK 丢失、
上报重试”各点退出后恢复；同一业务最多一份终态结果，重复结果不重复建单。
冻结结果传输晚到不因年龄被判无效；当前健康样本和历史结果分开校验。

### P4：恢复与账本

修改 `hardware/edge_boot.py`、`edge_store.py`、`work_manager.py`、`job_safety.py`、
`updater_store.py`、`updater_agent.py`、`local_control.py`；拟提取 `work_recovery.py`。
只有确实需要新的云端投影时才改 `communication_store.py` / `communication_router.py`，
不把正常串口迁入通信代理。

业务 SQLite 与更新器账本是不同存储，不假设跨进程一个事务能同时提交。
通过稳定动作/归档身份、本地待办、幂等调用及重启对账连接：保存恢复意图→登记原动作未知及
数据丢失→取得受限的新关门许可→登记新动作和真实反馈→更新本地恢复条件。
任何一步失败可重试核对，不能先放行全部动作或把原动作改为成功。

Pi 单独重启继续原业务；无回复持续查询并保留占用；确认 MCU 重启且必要数据丢失才分流。
投递问题归档与完整结果接收按同一业务串行裁定，避免两条终态各自产生效果。
清运保留原操作/人员/袋预留，恢复确认必须绑定本次恢复及新测量；不复用丢失前的按钮当新确认。
同 MCU 正常恢复或完整结果已保存时，不无故要求再按完成。

测试扩展 `test_job_safety.py`、`test_updater_store.py`、`test_edge_boot.py`、
`test_process_kill_recovery.py`、`test_stage4_startup_physical_recovery.py`、
`test_stage4_uart_v1_terminal_replay.py`、`test_stage4_uart_v1_post_arm_write_error.py`。
覆盖业务库/账本库分别提交后退出、旧结果与归档竞态、恢复关门回复丢失、更新互斥。
不重命名已有测试就声称新协议已被覆盖。

### P5：后端接收与数据效果

Java 文件以下按各模块 `src/main/java/org/enveloping/ecobin/` 为共同前缀：

- device：`device/application/delivery/TrustedDeliveryCompletionService.java`、
  `DeliveryRecoveryQuarantineService.java` 及 `device/api/result/`、`device/api/port/` 对应公开类型。
  复用证据存储而非复用手动隔离授权。新自动数据丢失入口名称在 P1 确定。
- recycling：`recycling/application/delivery/ApplyDeliveryCompleteService.java`、
  `deliveryorder/DeliveryReviewPolicy.java`、`clean/ApplyCleanCompleteService.java`、
  `device/AbortEdgeRestartedWorkService.java`、`ApplyCleanCommandObservationService.java`。
- integration：`integration/onenet/inbound/OneNetEventDispatcher.java`；必要的可靠处理进入
  operations 原有 inbox/任务机制，跨模块只走 `.api`，不跨表直写。
- 数据库：在 bootstrap 的 `src/main/resources/db/p0-migration/` 分配届时真实可用的新版本；
  核对约束、epoch guard、授权清单和迁移测试，不预先占用 V70 或修改已部署迁移。

三个投递分支分开验证：合法均值/中位数按既有审核规则；真正最终称重失败产生系统异常订单；
重启丢数据仅问题记录。最后一种在重复/乱序上报时均不能新增钱包明细、返现或自动提现任务。
异常订单人工审核后的资金行为沿用既有规则，不扩大成永远无法结算。
缺旧重量清运结果在一个权威事务中处理原操作、新袋、皮重、容量和占用，不补造旧净重。
新能力设备不能因 Pi 单独重启走旧清运中止路径；旧设备按可信版本继续明确处理。

扩展对应 `TrustedDeliveryCompletionWeightPolicyTest`、`TrustedDeliveryCompletionV2ParsingTest`、
`TrustedDeliveryCompletionServiceSqlTest`、`DeliveryRecoveryQuarantinePolicyTest`、
`ApplyDeliveryCompleteServiceSqlTest`、`ApplyCleanCompleteServiceSqlTest`、
`CleanCommandObservationDecisionTest`，补自动丢数据、清运恢复及真实 MySQL 并发测试。
H2/解析单测不代替真实 MySQL 的空值约束、唯一性、锁序和事务回滚证据。

### P6：界面与厂家验收适配

先处理新协议接入：`hardware/factory/acceptance_hardware.py` 当前直接依赖固定帧适配类型，
必须改成明确的能力接口，再修改 `acceptance_core.py`、`acceptance_measurements.py`、
`acceptance_service.py`、`hardware/factory/web/` 和必要的 `hardware/factory_seal/` 验证。
否则业务能跑但热点验收仍会失败。保留用户手填克数和每步数据展示，不自动校准或改回仅接受 500 g。
报告签名、配置摘要、前后端验收校验同步，不把运行时中位数规则直接替换厂家参考重量标准。

Web 检查 `frontend/web/src/pages/device-management/DeviceAssetDrawer.tsx`、
`delivery-orders/DeliveryOrderDetailDrawer.tsx`、`clean-operations/index.tsx`、
`clean-records/index.tsx` 及对应 API；小程序检查
`frontend/miniprogram/miniprogram/pages/delivery-entry/`、`clean-operation/`、`clean-record-detail/`。
只新增必要的缺失原因、原始证据、取值方式和恢复状态，不新建后端串口指挥台或重设计页面。

HMI 仍由用户操作上位机，以下是建议，不是已获确认的全部交互：

- page8.b0 保留 `printh 07`，表示同次清运再次开锁。
- b1 保留完成意图 `05`，建议去掉自行 `page page0`，由 MCU 处理后切页。
- b2 不能继续同发 `05`；建议独立返回事件，清运进行中禁用、未开始时允许返回。
  这一交互尚待用户确认，具体字节在全部事件查重后确定。
- 核对 page4～page7 所有按钮/定时脚本；截图中的 60 s 不自动覆盖既有配置等待时间，
  屏幕显示“获得余额”不能暗示云端已经入账。

实施时先交付逐控件脚本表和旧工程备份要求。屏幕无法下载不阻止纯软件开发，
但“返回/完成同一字节”不能被宣称已靠 MCU 判别解决；运行时隐藏控件也须实际验证后才可替代。
测试覆盖现有热点测量/报告套件和界面空值、中位数、手填参考重量；真机分别按完成与返回留证。

## 5. 验证顺序与验收证据

以下是实施时执行的命令/要求，本计划轮未运行测试。Windows 使用明确 Python 3.11，
不重建仓库中可能属于 Linux 的 `hardware/.venv`：

```powershell
$py = 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe'
& $py contracts/tools/generate_contracts.py --include-hardware-mcu
& $py contracts/tools/generate_contracts.py --check --include-hardware-mcu
& $py contracts/tools/validate_contracts.py
& $py -m unittest discover -s contracts/tests -v
& $py -m pytest hardware/tests/test_mcu_runtime_logic_c.py hardware/tests/test_uart_link_v1.py hardware/tests/test_edge_store.py hardware/tests/test_job_safety.py -q
& $py -m pytest hardware/tests -q
.\mvnw.cmd test
```

逐批先运行对应定向测试；新测试也要纳入，最后再完整回归。目标 Keil 构建、Linux/Pi 串口和
进程退出测试、MySQL 测试另记具体环境及命令。Java 使用 21；需要启动 bootstrap 时先
`.\mvnw.cmd install -DskipTests`，不读取旧模块 jar。Web 在其目录按现有脚本执行类型检查/构建及相关测试。
跳过项逐项说明，不能把 Windows 跳过的 Linux/真机测试算通过。

最低故障轨迹：

| 场景 | 应有证据与后续业务效果 |
|---|---|
| 开门受理回复丢失 | 原命令只下发一次，Pi 查询原作业，MCU 动作计数不增加 |
| Pi 重启而 MCU 继续 | 原业务身份不变、原结果补交，不要求重新扫码 |
| 保存前/后退出与确认丢失 | MCU 不提前清结果，Pi 不重复上报产生业务效果 |
| MCU 重启丢投递数据 | 记录实际已知证据与缺失原因；不声称已投物；无资金效果，新关门核对后可恢复 |
| MCU 重启但 Pi 已有完整结果 | 处理原结果，不降级成丢数据 |
| 清运丢旧数据 | 原清运员确认、新袋/新皮重有效，旧重量空值；后端只应用一次 |
| 云端清运应用确认丢失 | Pi 重试核对；不在旧袋/旧基准下接下一笔投递 |
| MCU 一直无回复 | Pi 本地持续查询、适度报告；不释放原占用，不要求后端逐步恢复 |
| CLOSE 防夹/解除/改 OPEN | 输出 00/01/10 与目标、有效动作一致，无取消动作复活；PB5 不单独阻断下一次开门 |
| 100 g / 101 g 波动与 5 s 中位数 | 分支正确、三端整数结果一致；中位数正常业务可用，不被旧后端校验拒绝 |
| 真正传感器断线与旧缓存 | 缺值而非零；非重启最终失败走系统异常订单，不冒充中位数 |
| 新旧版本混装/账本服务不可用 | 保留可诊断状态并拒绝新动作，不降级、不绕过许可、不抹历史 |

真机日志至少绑定固件摘要、Pi 包摘要、契约版本/能力、配置摘要、MCU 启动编号、业务/命令/
测量/结果身份、原始串口、逻辑目标与真实输出。称重保留本次原始读数、时间、最终取值方法。
仅 GPIO 输出不能证明真实门位，软件回归不能证明手部保护覆盖；现场测试不用人体试夹。

## 6. P7 发布、用户操作与回退

打包时同步检查 `hardware/install/runtime_payload_manifest.py`、
`hardware/system/business_runtime_preflight.py`、`hardware/edge_store_prepare.py` 和
device 模块的 `device/application/software/BusinessReleasePackageVerifier.java`：
新提取模块必须进入实际包和后端认可的文件清单，增量数据库准备与运行时版本检查一致。
本地源码测试通过但发布包漏文件，不算完成；永久层改动也不能只放进业务更新包。

1. 先在隔离环境验证后端向后兼容接收、必要数据库迁移及候选 OneNet 模型；
   获部署授权后再上线。需要导入物模型时给用户确切文件及差异，导入后只读核验。
2. 固定一组 MCU 固件、Pi 业务包、必要永久层包、HMI 工程、契约/物模型和配置摘要。
   原生 UART 与本地永久接口分别声明兼容范围；检查后端软件认可/允许列表，避免重现版本拒收。
3. 切换前停止接新业务，确认没有未交接结果、未完清运或未核对账本，备份身份/两类本地库/
   照片与配置；不打印或提交秘密。仍有工作时先处理原工作，不靠更新取消它。
4. 先准备设备端匹配包但保持停止接单；用户按交付清单手动烧录 MCU、按确认后的清单改 HMI。
   再激活匹配 Pi 包和确需更新的永久层接口。中途不匹配只拒绝业务，不自动尝试固定帧。
5. 获取新的维护连接和现场安全确认，先只读握手/配置/采样，再按范围进行单台 HIL
   （硬件在环，即实际 MCU、秤与机构参与）。完成投递、清运、重启、防夹和断线逐项留证。
6. 受控测试证明接单条件、结果和后台记录一致后再开放业务。原远程更新开关不随本次改造自动开启。

回退前先停止新业务并保存/交接所有可取得的结果。数据库尽量采用兼容的增量迁移；
若旧程序无法读取新记录，不做数据库降级或恢复过期快照丢掉新业务，而是保持停接单并前向修复。
允许回退时使用已验证的 MCU/Pi/HMI 匹配组合，永久层接口满足其版本要求；不是只回退一个文件。
清运的新袋/基准已经应用不能回滚成旧袋，永久账本不得回退为未执行。

通常不需要新 TF 镜像，优先保留数据的包升级；若另获授权制作镜像，复用 WSL
`/var/cache/ecobin-image-builder/` 受控依赖缓存，仍校验版本/摘要，不复用旧源码。

## 7. 待确认项与下一步

不再反复确认已接受的门方向、防夹、5 s 中位数和自动恢复原则。以下明确保留状态：

- 工程验证项：启动绑定防旧帧、拒旧序号规则、帧/缓存预算、称重新鲜度参数和实际 250 ms 响应。
  由代码/测试/实测解决；涉及增加硬件或 MCU 持久化才向用户请求新决定。
- 尚未确认的界面取舍：清运进行中禁用返回、完成由 MCU 切页。建议在 P6 开始前单独确认，
  不阻止 P0～P5 的独立工作，也不能在完整 HMI 发布验收时略过。
- 尚未确认的迟到结果取舍：已经按丢数据归档后又收到此前滞留完整结果。
  建议追加关联证据、保留原终态，不自动恢复结算；该资金相关规则需在 P4/P5 定稿前确认，
  未确认前只保存冲突，不自动产生价值。

建议下一步在用户明确授权实施后从 P0/P1 开始，同时先完成 P2 的独立时钟/防夹/称重逻辑。
首个可审查交付是“契约差异定稿 + 可验证状态轨迹 + 核心逻辑测试”，不是立刻烧录或部署。
