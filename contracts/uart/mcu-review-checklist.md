# EcoBin 原生 UART 2.0 MCU 候选确认单

> **当前候选（2026-09-14）**：UART `2.0.0-rc.26`，73种消息，115200/8N1，状态仍为
> `MCU_REVIEW_REQUIRED / SIMPLIFIED_BUSINESS_INTEGRATION_NOT_RELEASED`。MCU身份必须返回
> 真实能力位并覆盖`0x8100`；完整HMI指令组原子进入UART3软件发送队列即视为已显示，失败时
> 零字节发布且不推进显示状态。当前实现和验证边界见
> [rc.26收口记录](../../hardware/docs/review/uart2-rc26-capability-hmi-factory-projection-terminal-closure-2026-09-14.md)。
> 真实HMI、RS485、机构和断电HIL仍未执行，禁止依据下方历史候选数字放行。

> 最新候选 [P1BT：已授权未登记发送的恢复关门撤回](../../hardware/docs/review/uart2-recovery-close-withdrawal-p1bt-2026-09-13.md)：业务库先封住旧发送，永久层另存撤回事实并保留原授权历史；独立继任仅豁免准确祖先，候选循环已接自动核对，不发新动作、不启动云端、不恢复接单，业务39/永久3不变。
> 撤回专项53项与运行入口19项分别通过，最终集成/扩大回归见实施记录，完整契约25通过。已登记可能发送的未知效果、跨新启动号新动作、完整准入/云端/正常业务与main切换仍待接，P4/P5未完成。
> 未部署/烧录；OneNet2.2.0/UART rc.22/MySQL V79/权限40及发布25/39门槛不变。外部刷写/HIL不保证共用串口锁；RS485/HMI/完整固件容量保留。两项投递取舍已确认，下方历史“待确认”不再适用。

> 最新候选 [P1BH：后台确认可靠交接](../../hardware/docs/review/uart2-native-confirmation-p1bh-2026-09-13.md)：原始确认、报告与回执原子绑定，schema32；322项相关测试、25项完整契约检查通过。
> 投递重启缺最终包只归档、归档后迟到结果只追加证据，两项已获用户确认，不再待裁决；实现继续按P4/P5推进。确认交接不释放占用、不修改当前袋或准入，main尚未切换。
> 未部署/烧录；发布25/32、RS485与HMI等剩余门槛保留。以下为历史记录，历史“P4待确认”不再适用于上述两项决定。

> 最新硬件候选 [P1BG：原生上报与编号落库](../../hardware/docs/review/uart2-native-report-persistence-p1bg-2026-09-13.md)：实际 C 多轮/5 秒中位数/末重超时及照片快照已接可靠报告；缺测不补零，清运量为本次前重减后重。
> V78 只对齐四个业务测量编号，原质量/范围/空值约束保留；启动/供应目标同步，134 表/授权39不变。真实 MySQL 297 项、相关 Java 总计399项、Pi/隔离包50项、完整契约25项通过。
> 未部署/烧录；主程序、后端确认消费、当前袋/准入与完整异常仍待接。发布25/31、RS485、完整固件容量及 P4/HMI 待确认项保留，P3/P5/P7未整体完成。下方为历史。

> 2026-09-13 [P1BF](../../hardware/docs/review/uart2-business-report-p1bf-2026-09-13.md)：用户确认清运前后差值；已有前后测量足够，公式修改未改MCU/UART/HMI。Pi可靠上报入口schema31、OneNet2.1.3未部署；rc.22及MCU核心ROM63780/RAM6688限制保持，非完整可烧录固件。完整main/当前袋/确认消费仍待接。下方为历史。

> 最新硬件[P1BE原动作与结果核对](../../hardware/docs/review/uart2-result-execution-custody-p1be-2026-09-13.md)：Pi核对原许可/真实受理回复、正常首轮/继续投递/清运重开锁各自的输出与按钮顺序；命令与输出同时缺失也不漏掉原重开锁步骤。缺证据保留完整结果，不重放动作。
> 新增42项；专项与隔离包50项通过；扩大回归1722通过/35跳过/1失败（既有发布schema25/30不一致），不是全绿。契约574项/1694子用例及25项完整校验通过；rc.22、V77/授权39、OneNet2.1.2/schema30/3不变。
> 本批只读核对，不确认账本/上报/释放占用/产生资金；完整中断与恢复业务核对、持久消费/当前袋转换、完整准入/原生云端/HMI/main仍待接。P3/P5未整体完成，P7不可发布。
> 未改MCU源码，核心ROM63780/RAM6688、仅余1756字节ROM且不含完整固件的限制保持，不可烧录；P4裁决、RS485实测、发布25/30及SQLite1546问题保留。未部署/烧录，无需现场操作。下方为历史。

> 最新硬件[P1BB业务内采样组交接](../../hardware/docs/review/uart2-work-fullness-custody-p1bb-2026-09-13.md)：实际投递关门后/清运末重与原超声波组一起冻结并交接SQLite；时间各自保留，中断不伪造未满，丢确认不重采。清运仍需人工关门确认。
> rc.22，新增155项；契约574项/1694子用例及25项完整校验通过，扩大回归1320项通过。核心ROM63760/RAM6688，仅余1776字节ROM且不含完整main/HMI/UART，不可烧录。V77/授权39、OneNet2.1.2/schema30/3不变。
> 实际业务证据交接已接通；Pi原配置核对/当前袋转换、完整准入/原生云端/业务分类/HMI/main仍待接，P3/P5未整体完成、P7不可发布。P4裁决、RS485、发布25/30与SQLite1546问题保留；未部署/烧录，无需现场操作。下方为历史。

> 最新硬件[P1AY真实秤读取事实](../../hardware/docs/review/uart2-scale-observation-p1ay-2026-09-13.md)：投递/清运真实测量已同步最近原始读数与读取状态；保留实际采集/超时时刻，失败不复用旧值，阶段取消不冒充秤超时，不改历史测量。
> rc.21，新增27项/定向185项、契约441项/1694子用例及25项完整校验通过；扩大回归见记录。核心ROM54812/RAM6264仅链接探针，不可烧录。V77/授权39、OneNet2.1.2/schema30/3不变。
> 实际RS485归属、满溢采集、完整准入/原生云端/业务分类/HMI/main仍待接，P3/P5未整体完成、P7不可发布；P4资金取舍、发布25/30与SQLite1546问题保留。未部署/烧录，无需现场操作。下方为历史。

> 最新硬件[P1AX接单前环境事实](../../hardware/docs/review/uart2-environment-facts-p1ax-2026-09-13.md)：原生只读快照增加烟感/满溢来源与采集时间，真实烟感状态机已接发布接口；缺测不冒充正常，查询不刷新旧读数。实际满溢采集/准入/main仍待接。
> rc.21，新增26项/定向70项、扩大回归1199项通过；契约441项/1694子用例及25项完整校验通过。核心ROM54472/RAM6240仅链接探针，不可烧录。V77/授权39、OneNet2.1.2/schema30/3不变。
> 原生云端/业务分类/HMI及完整准入仍待接，P3/P5未整体完成、P7不可发布；P4资金取舍、RS485与发布25/30、SQLite1546问题保留。未部署/烧录，无需现场操作。下方为历史。

> 最新硬件[P1AK首轮动作账本核对](../../hardware/docs/review/uart2-action-reconciliation-p1ak-2026-09-12.md)：发送前保存原许可/动作/回执与命令，实际首重及两条输出核对后持久确认；Pi重启自主找回待办，不重发开门、不清业务占用、不产生资金。
> rc.20/schema28，新增72项专项，最终108项定向及25项完整契约验证通过；扩大回归1802通过/1失败/5跳过/1636子测试通过，失败为既有Windows SQLite强杀恢复1546，已留证但未解决。
> 首次正常动作核对已本地接通；再次清运开锁/异常与恢复许可/分类/HMI/main仍待接，P3/P4未整体完成。未部署/烧录，当前无需用户操作。下方为历史。

> 最新硬件[P1AJ清运中断事实交接](../../hardware/docs/review/uart2-clean-interruption-p1aj-2026-09-12.md)：更新停止/到期/控制状态丢失/受理后未通电分别留因，保留原输出与末重；Pi精确保存后仍维持原清运恢复占用，不推定门关闭。
> rc.20/schema27，517项相关及25项完整契约验证通过；扩大回归1731通过/5跳过/1636子测试通过/零失败，ROM51648/RAM6096仅核心探针，历史SQLite偶发1546未宣称修复。恢复执行/账本/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，无需当前用户操作。下方为历史。

本确认单用于新原生协议的候选评审，尚不允许部署或接单。唯一可变机器来源是
[`uart-registry.yaml`](uart-registry.yaml)，展开后的偏移、长度和摘要见
[`generated/uart-layout.json`](generated/uart-layout.json)。本文件只记录评审结论，不在
这里另建第二套消息号或字段定义。

## 1. 历史阶段记录

> 最新硬件[P1AI清运解锁前称重失败](../../hardware/docs/review/uart2-clean-preunlock-failure-p1ai-2026-09-12.md)：首重无结果/中断精确保存后冻结FAILED；零解锁步骤、末重未采集、不伪造人工确认，Pi重启/丢回执不重放、不清占用，上一单数据不串单。
> rc.19/schema27不变，300项相关及25项完整契约验证通过；扩大回归1668通过/5跳过/1635子测试通过/零失败，ROM50548/RAM6064仅核心探针，历史SQLite偶发1546未宣称修复。已解锁异常/账本/恢复/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，当前无需用户操作。下方为历史。

> 最新硬件[P1AH清运人工确认与结果交接](../../hardware/docs/review/uart2-clean-confirmation-p1ah-2026-09-12.md)：当前末重与人工关门确认分别精确保存后冻结结果；重开锁不复用旧重量，Pi重启/丢确认不重放、不清占用，中断结果保留失败。
> rc.19/schema27，176项相关及25项完整契约验证通过；扩大回归1631通过/5跳过/1635子测试通过/1失败（既有Windows SQLite强杀恢复1546，未修复），ROM50196/RAM6064仅核心探针。完整清运异常/账本/恢复/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，当前无需用户操作。下方为历史。

> 最新硬件[P1AG清运意图与独立末重](../../hardware/docs/review/uart2-clean-intent-p1ag-2026-09-12.md)：原按钮请求精确保存后再次单次开锁/独立称重；重新开锁作废旧候选但保留原文，Pi重启/丢确认不重放，不清业务占用。
> rc.18/schema26，126项相关及25项完整契约验证通过；扩大回归1533通过/5跳过/1635子测试通过/零失败，ROM48488/RAM6048仅核心探针。人工关门确认/最终结果、完整异常/账本/恢复/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，无需当前用户操作。下方为历史。

最新运行增量[P1AF](../../hardware/docs/review/uart2-clean-first-unlock-p1af-2026-09-12.md)：实际C首次清运单次通电、独立断电、双记录及Pi重启/丢确认交接通过27项专项、25项契约检查；扩大回归见记录。
rc.17/schema25不变，ROM45828/RAM5952仅探针；再次开锁/完成/末重/HMI、完整异常/账本/恢复/main未完成，不推断门位或清Pi占用，未部署/烧录。下方为历史。

最新[P1AE](../../hardware/docs/review/uart2-postclose-interruption-p1ae-2026-09-12.md)：自动检测CLOSE后更新停止/控制上下文丢失，独立原因及原输出/测量/选择保存后FAILED；Pi重启不重放或清占用。
192项相关、25项契约检查通过；扩大回归1412通过/5跳过/1635子测试通过/零失败。ROM44072/RAM5744仅探针；清运/HMI、完整异常/账本/恢复/main未完成，未部署/烧录。下方为历史。

最新[P1AD](../../hardware/docs/review/uart2-interrupted-measurement-p1ad-2026-09-12.md)：独立中断测量与首末失败结果精确交接，Pi重启/丢确认不重放、不清占用；176项定向、25项完整契约检查通过。
ROM42832/RAM5720仅核心探针，扩大回归/静态告警见记录；自动关门后异常触发/原因事件及完整业务/main未接，未部署/烧录。下方为历史。

最新[P1AC](../../hardware/docs/review/uart2-delivery-abort-p1ac-2026-09-12.md)：CLOSE前中断独立原因及原输出精确保存后形成FAILED；连续第二轮/Pi重启/丢确认已验证，不补造末重/关门或清Pi占用。
234项定向、25项完整契约验证通过，ROM42244/RAM5720仅核心探针，扩大回归见记录；完整异常/清运/HMI/账本/恢复/main仍未完成，不可部署/烧录。下方为历史。

最新运行增量[P1AB](../../hardware/docs/review/uart2-delivery-preopen-failure-p1ab-2026-09-12.md)：首重失败精确保存后冻结FAILED/零轮次/末重未采集，实际C/Pi/SQLite交接和重启验证通过；11项专项、140项相关回归。
rc.14/schema25不变，ROM41308/RAM5712仅核心探针；扩大回归/SQLite取证见记录。完整异常/清运/HMI/账本/恢复/main未完成，不清Pi占用，不可部署/烧录。下方为历史。

最新[P1AA](../../hardware/docs/review/uart2-delivery-local-continue-p1aa-2026-09-12.md)：原会话连续投递、逐轮异常累计和本地来源动作精确交接已验证；82项相关组合、25项契约验证通过，最终扩大回归1249通过/5跳过/1625子测试通过/1失败（既有Windows SQLite强杀恢复1546错误，未修复）。
ROM40952/RAM5712仅核心探针；清运/HMI、完整异常终态、账本效果核对/恢复/分类/main待接，未部署/烧录。以下P1Z段落为前批记录。

最新运行增量[P1Z](../../hardware/docs/review/uart2-delivery-finalization-p1z-2026-09-12.md)已接C原配置窗口/按钮/超时、选择精确保存后的首次首末结果组装及Pi交接；19项专项、92项相关回归通过。
ROM40340/RAM5672仅核心链接探针。CONTINUE只保留选择；下一轮实际动作、逐轮异常累计、HMI、清运、账本/恢复/分类/main未完成，rc.13/schema24不变，不可部署/烧录。

| 项目 | 当前值 |
|---|---|
| 候选版本 | `2.0.0-rc.19` |
| 线协议 | major `2` / minor `0`，与旧固定帧 v2 不是同一协议 |
| 物理串口 | `115200 / 8N1 / no flow control` |
| Registry 状态 | `MCU_REVIEW_REQUIRED` / `CLEAN_CONFIRMATION_CUSTODY_NOT_RUNNABLE` |
| 软件生成/校验 | 共66条消息、55条完整校验，49帧/1477流轨迹/15摘要样例（6种规则），104生成物；P1AI首重失败不开锁/原结果精确交接，保留P1AH人工确认；候选schema27，HMI、完整异常/账本效果核销/恢复/分类/main仍未接 |
| MCU 逐字段确认 | 结果槽/原业务/单投口事实局部验证；真实采集/配置/业务与主程序接线未完成 |
| C 固件工具链黄金样本 | 黄金程序通用 C11 通过；独立会话核心另有 ARMCC 编译证据，无目标板运行 |
| 真机/HIL | 新候选未执行，禁止据旧证据放行 |

历史 F-10 收口不代表新候选完成。既有 3 个运行生成制品在 `frozen-v1-runtime.json`
中冻结摘要；候选生成到contracts及独立hardware/uart2_protocol.py，不覆盖旧文件；
`--include-hardware-mcu`当前报错。schema27及专用待分类上报队列不等于业务上报已接通。
最新运行证据见[P1AI](../../hardware/docs/review/uart2-clean-preunlock-failure-p1ai-2026-09-12.md)，契约仍沿用[P1AH](../../hardware/docs/review/uart2-clean-confirmation-p1ah-2026-09-12.md)。ARMCC需编译生成ecobin_uart_protocol.c一次；以下为历史增量。
随后[P1T](../../hardware/docs/review/uart2-actuator-journal-p1t-2026-09-12.md)新增C八条动作内存预留/保留/逐条释放；
[P1U](../../hardware/docs/review/uart2-actuator-credits-p1u-2026-09-12.md)已接控制入口唯一记录池与共享编号额度；P1V再接实际C查询与Pi事务提交后的精确保存，117项相关测试通过。
后续[P1W](../../hardware/docs/review/uart2-delivery-execution-p1w-2026-09-12.md)已接首次投递原命令核对、预留后单次执行、100ms双断开与中断自动关门；11项执行专项/53项定向组合通过，含Pi/永久账本/C/SQLite联合测试。
P1X已接关门后独立测量，P1Y已接选择原轮次关联/精确交接；最新核心只链接探针ROM39024/RAM5640字节，不是生产固件/完整栈证据。
rc.13/schema24；选择产生/HMI/继续与结束、完整结果、清运、账本核销/恢复和正常main仍未接通。
动作报文合法不等于已落盘，已落盘也不等于真实账本效果核对完成或可以执行另一动作；
清运锁通电和断电分别保留/交接，断电不得等待确认。OneNet/后端仍旧配置，须同步配对切换。
下列未勾选项是后续审查要求，不因字段仍保留在候选中而视为已实现。

## 2. 评审输入

MCU 负责人应取得同一份仓库状态中的：

- `contracts/uart/uart-registry.yaml`；
- `contracts/uart/generated/uart-layout.json`；
- `contracts/uart/generated/c/ecobin_uart_protocol.h`；
- `contracts/uart/generated/c/ecobin_uart_golden_test.c`；
- `contracts/examples/uart/golden-vectors.json`；
- `contracts/examples/uart/stream-traces.json`；
- `contracts/examples/uart/digest-vectors.json`；
- `contracts/generated/contract-catalog.md`；
- `docs/planning/interface-design/10-uart-protocol-i046-i050.md`。

生成文件头中的 Registry SHA-256 必须与 `golden-vectors.json` 相同。摘要不一致时停止评审，
重新运行生成器，不能手工改 C 头文件。

## 3. 必须逐项确认

### 3.1 帧与传输

- [ ] 串口固定为 `115200 baud、8 data bits、no parity、1 stop bit、no flow control`；
  设备路径由香橙派部署配置，不写进业务消息。
- [ ] magic 为 `EC 42`，多字节整数为 big-endian。
- [ ] 完整帧最长 256 字节，payload 最长 242 字节，接收缓冲硬上限 512 字节。
- [ ] CRC 为 CRC-16/CCITT-FALSE，`123456789 → 29B1`。
- [ ] 候选帧接收期限 100 ms；普通 ACK 期限 500 ms。通用最多 3 次规则不适用于
  引导和命令：`sessionPolicy` 覆盖为仅发送一次，超时重新询问/查询原命令，禁止旧绑定重发。
- [ ] CRC 通过前不相信版本、flags、消息号、序号或 payload。
- [ ] 通用帧 ACK、命令受理、机械输出和业务完成分别表达；ACK 不释放完整业务结果。
- [ ] 单次读取超过 512 字节或已有半帧时，按剩余容量追加并排空，不裁掉前部合法帧；
  候选三语言软件轨迹已通过，实际 UART 输入/调度仍须验证。

### 3.2 消息、字段与能力

- [ ] 55 个 message type 的编号、方向和 ACK 规则无冲突；消息完整性仍需结合后续批次。
- [ ] 4 条引导消息只用一次性持久询问编号；绑定回复的状态与当前/拟分配编号一致。
- [ ] 15 条命令的 60 字节身份前缀含 UUID、摘要、目标启动编号和持久命令序号；
  后两项纳入 v2 命令摘要。传输序号不是命令序号，不能替代去重身份。
- [ ] 命令决定和查询完整回显原身份/摘要；旧详情不可取不等于从未执行，查询不推进动作序号。
- [ ] 完整结果保存确认包含原启动/结果序号/业务/完整摘要，实际 SQLite 提交前不得发送；
  结果正文/冻结槽及实际SQLite事务已独立验证，主程序接线和业务分类尚未完成。
- [ ] DEVICE_FACTS_REPLY单投口事实正文204字节/总帧218字节；门目标/PB5/输出来自同次控制刷新，
  不从原始引脚电平推导实测门位；原始读数采集时间与最后测量/所用配置单列，过期不能冒充当前读数。
  本批实际C/Python已验证；未含烟雾/红外，不能据此省略完整安全/接单检查。
- [ ] 每条固定 payload 的字段顺序、偏移、宽度、符号和范围可由 MCU 实现。
- [ ] UUID 为 16 字节网络顺序，SHA-256 为 32 字节原值，重量为带符号 `int32` 克。
- [ ] UUID 默认禁止全零；仅 Registry 明示的可空作业、最新命令和 staging sentinel
  按条件使用全零编码。
- [ ] 单价为“元/千克 × 10000”的 `uint32`，所有时长为整数毫秒。
- [ ] capability bit 0～14 与目标板能力一致；握手基线是 `0x300`，完整已知掩码是
  `0x7fff`。未实现时不得宣称持久去重/事件或 `DELIVERY_DOOR_HIL_QUALIFIED`，也没有
  清运门门磁、清运门自动关门或清运 `SAFE_CLOSE` 能力。
- [ ] 配置分段固定为 `BEGIN(1) → DEVICE(2) → PORT(3..N+2) → COMMIT(N+3)`，
  `partCount=N+3`，N 为投口数。
- [ ] 状态快照固定为 `BEGIN(1) → PORT(2..N+1) → END(N+2)`，
  `partCount=N+2`，整份摘要通过后才可应用。
- [ ] `PortFaultBitmap` 只登记 bit 0、1、2、4，其他位必须为 0；bit 0 来自最近门命令
  输出失败，bit 1/2/4 来自对应 health。超声波无回波/样本不足不形成故障位。
- [ ] 快照 applied 配置使用“version=0 + 两个全零摘要”或“version>0 + 两个非零摘要”；
  staging 有效时 `stagingPartCount=portCount+3`、bit 0 已置位且高于 partCount 的位为 0。

### 3.3 投递与清运状态机

- [ ] 一个 `sessionUid` 可在 MCU 本地执行多个继续轮次，但只形成整场首重、末重和一次
  OneNet `DELIVERY_COMPLETE`。
- [ ] 中间按钮、门事件、重量和减少值只留在边缘恢复上下文，不上 OneNet。
- [ ] 任一轮次减少达到冻结阈值时只锁存布尔 `negativeWeightAnomaly`。
- [ ] `CLEAN_LOCK_POWER_CHANGED` 只表达电磁阀 `ENERGIZED/DEENERGIZED`，不能生成
  `DOOR_OPENED/DOOR_CLOSED`。
- [ ] `CLEAN_FINISH_REQUESTED` 只是清运员按钮/确认请求，不是完成事实。
- [ ] `CLEAN_FINAL_WEIGHT_READY` 用于完成请求后的最终称重结果（含 `UNSTABLE` 兜底值）
  或明确终态称重失败；
  MCU 负责人确认该独立消息与屏幕状态机匹配。
- [ ] 测量字段尚待下一切片同步：250 ms 目标采样，最近 5 点全跨度 ≤100 g 取均值，
  最多 5 s 纯波动取中位数；不能沿用候选暂存的旧末四次均值规则宣称实现新策略。
- [ ] 投递门 OPEN=`PB6=1、PB7=0`，CLOSE=`PB6=0、PB7=1`。
  最近有效目标与实际输出分开；PB5 仅暂停关门，释放继续；开门忽略，PB4 未接。
  PB5 不证明到位或故障，不以旧确认单错误的反向映射/100 ms 假定覆盖当前核心实现。
- [ ] `DELIVERY_DOOR_COMMAND_RESULT` 和 `SAFE_CLOSE_RESULT` 只报告方向与输出结果，
  不携带脉冲时长；物理门位始终 `NOT_OBSERVABLE`。
- [ ] 已受理但尚未实际下发的旧方向命令被新方向取代时报告
  `COMMAND_SUPERSEDED_BEFORE_DISPATCH`，不得再使用
  `PARTIAL_OUTPUT_INTERRUPTED`。
- [ ] `SAFE_CLOSE` 只控制投递门；清运恢复最多令电磁阀断电并等待原清运员现场确认。

### 3.4 挥发状态、启动绑定与重启

- [ ] MCU 启动编号 RAM 归零；香橙派先持久分配再下发非零编号。MCU 已绑定时不重绑，
  香橙派独自重启不换 MCU 编号。编号空间耗尽不回绕，持久账本不回退/复用。
- [ ] 请求可丢失/延迟/乱序但不能复制的模型前提须由实际单次发送链路证明；
  单独收到绑定回复不能证明此后未重启，每条业务命令仍核对精确目标启动编号。
- [ ] 完整结果保留至香橙派完整校验、SQLite 提交并明确保存确认；通用 ACK 不释放。
  结果分段/保存确认消息尚未落地，不能把这一要求写成已实现。
- [ ] 配置 staging 与 COMMIT 在 RAM 中原子切换，半份配置不会成为 `APPLIED`；重启后
  配置回到 `EMPTY` 并由香橙派重新下发。
- [ ] MCU 重启先停 PB6/PB7/PB8，不自动重放旧动作。真正丢失必要投递数据只留
  无业务价值的问题记录；香橙派发新关门动作并检查后自动接单，已保存完整结果不作废。
- [ ] 清运必要旧数据丢失时保持原操作和清运员，完成新袋/关门确认/新皮重后按独立
  恢复结果上报；不得用新数据代填缺失旧数据。相关跨端消息/状态机仍待实施。
- [ ] MCU 不新增 Flash 启动编号或结果存储；未实现持久能力不得宣称支持。

## 4. C 黄金样本

在实际 MCU 编译器或与其 ABI/整数模型等价的 C11 工具链中编译：

```text
<cc> -std=c11 -Wall -Wextra -Werror \
  -Icontracts/uart/generated/c \
  contracts/uart/generated/c/ecobin_uart_golden_test.c \
  -o ecobin_uart_golden_test
```

预期输出：

```text
C UART golden vectors: 24 frames, 136 stream traces, 3 digests passed
```

还需记录编译器名称/版本、目标架构、命令、输出和 Registry SHA-256。仅在普通 PC
编译通过不能替代目标固件工具链确认。

## 5. 评审结论

| 项目 | 填写 |
|---|---|
| MCU 负责人 |  |
| 日期/时区 |  |
| MCU/板卡型号 |  |
| 固件工具链与版本 |  |
| 可实现的 capability bitmap |  |
| 非易失介质与保留能力 |  |
| C 黄金样本结果/证据位置 |  |
| 需修改的消息/字段 |  |
| 结论 | `APPROVED` / `CHANGES_REQUIRED` |

若为 `CHANGES_REQUIRED`，先修改唯一 Registry、重新生成候选，再重新评审，不得私改生成物。
只有 P0～P7 所需字段、双方运行接线、保留数据迁移、资源和实机证据齐全，才可另行
审查成对发布；本次身份切片通过不解除任何设备部署门槛。以下第 6～7 节仅保留历史证据。

## 6. 2026-07-25 rc.2 局部真机证据

- 真实 HELLO：`stm32f103rct6` / `1.0.0-hil.3` / capability `0x300` / 一投口。
- 当时的配置 BEGIN/DEVICE/PORT/COMMIT、独立 APPLY_RESULT、重复 COMMIT 去重和完整
  QUERY_STATE 摘要校验通过；rc.3 已改变配置字段、摘要和快照布局，必须重新执行。
- MCU boot ID 已限制在 `1..9007199254740991`；空关键事件队列的 oldest/latest
  四个范围字段均严格为零。
- 详细命令、结果和未关闭边界曾在独立 F-11 worktree 留存；该历史切片不代表当前
  固定帧线路或完整 `uart-v1` 部署验收。
- MCU 实际 ARMCC/Keil 尚未单独执行生成的 C 黄金程序；HELLO 的 RCT6 identity 与
  Keil target 名称中的 C8 也待核对。因此本节只保存局部历史证据，不关闭当前固定帧
  H-03，也不证明完整 `uart-v1` 部署符合性。

## 7. 2026-07-26 持续锁存门控破坏性变更

- `CONFIG_DEVICE_BLOCK` 已删除 `deliveryDoorOpenCommandSignalMs` 和
  `deliveryDoorCloseCommandSignalMs`，固定 payload 长度为 163 字节。
- `DELIVERY_DOOR_COMMAND_RESULT`、`SAFE_CLOSE_RESULT` 已删除 `actualOutputMs`，
  固定 payload 长度分别为 60 和 43 字节；`STATE_SNAPSHOT_PORT` 已删除
  `lastDeliveryDoorActualOutputMs`，固定 payload 长度为 81 字节。
- `mcuPayloadSha256` 摘要输入、UART 黄金向量、OneNet 配置/事件投影和三端生成物已随
  上述布局重建。旧香橙派与新 MCU（或新香橙派与旧 MCU）不得混用，否则配置长度、
  摘要或门结果解码会不一致。
