# EcoBin 机器契约

> 当前采用[精简实施计划](../docs/planning/mcu-edge-simplified-implementation-plan-2026-09-13.md)的UART rc.23：START授权MCU自主业务，完整结果可靠交接；不再将逐动作或过程记录确认作为推进条件。199字节最终包布局与历史解码保留，新报告使用v2语义，旧v1报告只读不重写。两端完整接通和现场验证尚未完成；以下P1复杂恢复进度仅为历史，不继续扩展。

> OneNet候选2.3.0：APPLY_CONFIGURATION新增明确的UART_V2_SIMPLIFIED档位，原v1摘要保留；固定采样参数由Java/Pi按档位展开到原生UART，不增加管理员填写项。111份生成物一致、完整契约25项通过。既有runtimeSnapshot.ports的34成员结构仍需S4整改/实际导入验证，不能把这次本地校验当成OneNet现场验收。详见[入口与配置记录](../hardware/docs/review/native-business-entry-s1-2026-09-13.md)。

> 最新候选 [P1BT：已授权未登记发送的恢复关门撤回](../hardware/docs/review/uart2-recovery-close-withdrawal-p1bt-2026-09-13.md)：业务库先封住旧发送，永久层另存撤回事实并保留原授权历史；独立继任仅豁免准确祖先，候选循环已接自动核对，不发新动作、不启动云端、不恢复接单，业务39/永久3不变。
> 撤回专项53项与运行入口19项分别通过，最终集成/扩大回归见实施记录，完整契约25通过。已登记可能发送的未知效果、跨新启动号新动作、完整准入/云端/正常业务与main切换仍待接，P4/P5未完成。
> 未部署/烧录；OneNet2.2.0/UART rc.22/MySQL V79/权限40及发布25/39门槛不变。外部刷写/HIL不保证共用串口锁；RS485/HMI/完整固件容量保留。两项投递取舍已确认，下方历史“待确认”不再适用。

> 最新候选 [P1BH：后台确认可靠交接](../hardware/docs/review/uart2-native-confirmation-p1bh-2026-09-13.md)：原始确认、报告与回执原子绑定，schema32；322项相关测试、25项完整契约检查通过。
> 投递重启缺最终包只归档、归档后迟到结果只追加证据，两项已获用户确认，不再待裁决；实现继续按P4/P5推进。确认交接不释放占用、不修改当前袋或准入，main尚未切换。
> 未部署/烧录；发布25/32、RS485与HMI等剩余门槛保留。以下为历史记录，历史“P4待确认”不再适用于上述两项决定。

> 最新硬件候选 [P1BG：原生上报与编号落库](../hardware/docs/review/uart2-native-report-persistence-p1bg-2026-09-13.md)：实际 C 多轮/5 秒中位数/末重超时及照片快照已接可靠报告；缺测不补零，清运量为本次前重减后重。
> V78 只对齐四个业务测量编号，原质量/范围/空值约束保留；启动/供应目标同步，134 表/授权39不变。真实 MySQL 297 项、相关 Java 总计399项、Pi/隔离包50项、完整契约25项通过。
> 未部署/烧录；主程序、后端确认消费、当前袋/准入与完整异常仍待接。发布25/31、RS485、完整固件容量及 P4/HMI 待确认项保留，P3/P5/P7未整体完成。下方为历史。

> 最新：[P1BF原生业务上报与清运前后差值](../hardware/docs/review/uart2-business-report-p1bf-2026-09-13.md)。OneNet候选2.1.3保留设备作用域的EBM1原测量编号并校验启动/事件身份；清运量按用户最新决定取本次前后差值。UART rc.22不变，25项完整合同校验通过。Pi schema31可靠上报入口未部署，发布声明25/31不匹配仍阻止发布。下方为历史。

> 最新硬件[P1BE原动作与结果核对](../hardware/docs/review/uart2-result-execution-custody-p1be-2026-09-13.md)：Pi核对原许可/真实受理回复、正常首轮/继续投递/清运重开锁各自的输出与按钮顺序；命令与输出同时缺失也不漏掉原重开锁步骤。缺证据保留完整结果，不重放动作。
> 新增42项；专项与隔离包50项通过；扩大回归1722通过/35跳过/1失败（既有发布schema25/30不一致），不是全绿。契约574项/1694子用例及25项完整校验通过；rc.22、V77/授权39、OneNet2.1.2/schema30/3不变。
> 本批只读核对，不确认账本/上报/释放占用/产生资金；完整中断与恢复业务核对、持久消费/当前袋转换、完整准入/原生云端/HMI/main仍待接。P3/P5未整体完成，P7不可发布。
> 未改MCU源码，核心ROM63780/RAM6688、仅余1756字节ROM且不含完整固件的限制保持，不可烧录；P4裁决、RS485实测、发布25/30及SQLite1546问题保留。未部署/烧录，无需现场操作。下方为历史。

> 最新硬件[P1BB业务内采样组交接](../hardware/docs/review/uart2-work-fullness-custody-p1bb-2026-09-13.md)：实际投递关门后/清运末重与原超声波组一起冻结并交接SQLite；时间各自保留，中断不伪造未满，丢确认不重采。清运仍需人工关门确认。
> rc.22，新增155项；契约574项/1694子用例及25项完整校验通过，扩大回归1320项通过。核心ROM63760/RAM6688，仅余1776字节ROM且不含完整main/HMI/UART，不可烧录。V77/授权39、OneNet2.1.2/schema30/3不变。
> 实际业务证据交接已接通；Pi原配置核对/当前袋转换、完整准入/原生云端/业务分类/HMI/main仍待接，P3/P5未整体完成、P7不可发布。P4裁决、RS485、发布25/30与SQLite1546问题保留；未部署/烧录，无需现场操作。下方为历史。

> 最新硬件[P1AY真实秤读取事实](../hardware/docs/review/uart2-scale-observation-p1ay-2026-09-13.md)：投递/清运真实测量已同步最近原始读数与读取状态；保留实际采集/超时时刻，失败不复用旧值，阶段取消不冒充秤超时，不改历史测量。
> rc.21，新增27项/定向185项、契约441项/1694子用例及25项完整校验通过；扩大回归见记录。核心ROM54812/RAM6264仅链接探针，不可烧录。V77/授权39、OneNet2.1.2/schema30/3不变。
> 实际RS485归属、满溢采集、完整准入/原生云端/业务分类/HMI/main仍待接，P3/P5未整体完成、P7不可发布；P4资金取舍、发布25/30与SQLite1546问题保留。未部署/烧录，无需现场操作。下方为历史。

> 最新硬件[P1AX接单前环境事实](../hardware/docs/review/uart2-environment-facts-p1ax-2026-09-13.md)：原生只读快照增加烟感/满溢来源与采集时间，真实烟感状态机已接发布接口；缺测不冒充正常，查询不刷新旧读数。实际满溢采集/准入/main仍待接。
> rc.21，新增26项/定向70项、扩大回归1199项通过；契约441项/1694子用例及25项完整校验通过。核心ROM54472/RAM6240仅链接探针，不可烧录。V77/授权39、OneNet2.1.2/schema30/3不变。
> 原生云端/业务分类/HMI及完整准入仍待接，P3/P5未整体完成、P7不可发布；P4资金取舍、RS485与发布25/30、SQLite1546问题保留。未部署/烧录，无需现场操作。下方为历史。

> 最新硬件[P1AK首轮动作账本核对](../hardware/docs/review/uart2-action-reconciliation-p1ak-2026-09-12.md)：发送前保存原许可/动作/回执与命令，实际首重及两条输出核对后持久确认；Pi重启自主找回待办，不重发开门、不清业务占用、不产生资金。
> rc.20/schema28，新增72项专项，最终108项定向及25项完整契约验证通过；扩大回归1802通过/1失败/5跳过/1636子测试通过，失败为既有Windows SQLite强杀恢复1546，已留证但未解决。
> 首次正常动作核对已本地接通；再次清运开锁/异常与恢复许可/分类/HMI/main仍待接，P3/P4未整体完成。未部署/烧录，当前无需用户操作。下方为历史。

> 最新硬件[P1AJ清运中断事实交接](../hardware/docs/review/uart2-clean-interruption-p1aj-2026-09-12.md)：更新停止/到期/控制状态丢失/受理后未通电分别留因，保留原输出与末重；Pi精确保存后仍维持原清运恢复占用，不推定门关闭。
> rc.20/schema27，517项相关及25项完整契约验证通过；扩大回归1731通过/5跳过/1636子测试通过/零失败，ROM51648/RAM6096仅核心探针，历史SQLite偶发1546未宣称修复。恢复执行/账本/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，无需当前用户操作。下方为历史。

本目录保存跨端协议的唯一机器来源。业务语义仍以 `docs/planning/` 下已确认的需求、接口和
详细设计为准；这里负责把字段、单位、枚举、线级编号和校验规则变成可解析、可生成、
可重复验证的制品。

## 当前状态

> 最新硬件[P1AI清运解锁前称重失败](../hardware/docs/review/uart2-clean-preunlock-failure-p1ai-2026-09-12.md)：首重无结果/中断精确保存后冻结FAILED；零解锁步骤、末重未采集、不伪造人工确认，Pi重启/丢回执不重放、不清占用，上一单数据不串单。
> rc.19/schema27不变，300项相关及25项完整契约验证通过；扩大回归1668通过/5跳过/1635子测试通过/零失败，ROM50548/RAM6064仅核心探针，历史SQLite偶发1546未宣称修复。已解锁异常/账本/恢复/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，当前无需用户操作。下方为历史。

> 最新硬件[P1AH清运人工确认与结果交接](../hardware/docs/review/uart2-clean-confirmation-p1ah-2026-09-12.md)：当前末重与人工关门确认分别精确保存后冻结结果；重开锁不复用旧重量，Pi重启/丢确认不重放、不清占用，中断结果保留失败。
> rc.19/schema27，176项相关及25项完整契约验证通过；扩大回归1631通过/5跳过/1635子测试通过/1失败（既有Windows SQLite强杀恢复1546，未修复），ROM50196/RAM6064仅核心探针。完整清运异常/账本/恢复/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，当前无需用户操作。下方为历史。

> 最新硬件[P1AG清运意图与独立末重](../hardware/docs/review/uart2-clean-intent-p1ag-2026-09-12.md)：原按钮请求精确保存后再次单次开锁/独立称重；重新开锁作废旧候选但保留原文，Pi重启/丢确认不重放，不清业务占用。
> rc.18/schema26，126项相关及25项完整契约验证通过；扩大回归1533通过/5跳过/1635子测试通过/零失败，ROM48488/RAM6048仅核心探针。人工关门确认/最终结果、完整异常/账本/恢复/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，无需当前用户操作。下方为历史。

> 最新运行增量[P1AF首次清运开锁](../hardware/docs/review/uart2-clean-first-unlock-p1af-2026-09-12.md)：复用原首次开锁/锁通断消息，双记录预留后单次执行、独立断电和Pi精确保存已验证；27项专项及25项契约检查通过，扩大回归见记录。
> rc.17/schema25和104生成物不变；再次开锁/完成/清运末重/HMI、完整异常/账本/恢复/main待接，不清Pi占用、不推断门位，未部署/烧录。下方为历史。

> 最新[P1AE关门后中断交接](../hardware/docs/review/uart2-postclose-interruption-p1ae-2026-09-12.md)：rc.17 / POSTCLOSE_INTERRUPT_NOT_RUNNABLE，schema25不变；新增61字节独立原因事件和严格阶段/测量引用校验，实际C/Pi保存后失败收尾，不清Pi占用。
> 66消息/52守卫，47帧/1421流轨迹/12摘要、104生成物；192项相关及25项完整契约检查通过，扩大回归1412通过/5跳过/1635子测试通过/零失败。清运/HMI、完整异常/账本/恢复/main未完成，未部署/烧录。下方为历史。

> 最新[P1AD测量中断交接](../hardware/docs/review/uart2-interrupted-measurement-p1ad-2026-09-12.md)：rc.16 / MEASUREMENT_INTERRUPT_NOT_RUNNABLE，schema25不变。中断种类/原因必须配对，不伪装正常最终结果；实际C/Pi保存交接后失败收尾，不清Pi占用。
> 65消息/51守卫，46帧/1346流轨迹/11摘要、104生成物；176项定向和25项完整契约检查通过，扩大回归及静态告警见记录。自动关门后异常触发/清运/HMI/账本/恢复/main未完成，未部署/烧录。下方为历史。

> 最新[P1AC](../hardware/docs/review/uart2-delivery-abort-p1ac-2026-09-12.md)：rc.15 / DELIVERY_ABORT_CUSTODY_NOT_RUNNABLE；新增61字节中断原因事件，实际C/Pi精确交接后形成失败结果，不造关门/末重、不清Pi占用。
> schema25不变，65消息/51守卫，45帧/1318流轨迹/11摘要、104生成物；234项定向及25项完整契约检查通过，扩大回归见记录。完整异常/清运/HMI/账本/恢复/main未完成，未部署/烧录。下方为历史。

> 最新运行增量[P1AB](../hardware/docs/review/uart2-delivery-preopen-failure-p1ab-2026-09-12.md)：首次称重失败的原文保存、零轮次/末重未采集最终交接已接通；11项专项、140项相关回归通过，扩大回归及SQLite取证见记录。
> rc.14/schema25及生成物不变；Pi占用保持、不产生资金，完整异常/清运/HMI/恢复/main仍待接，未部署/烧录。下方为历史。

> 最新[P1AA连续投递](../hardware/docs/review/uart2-delivery-local-continue-p1aa-2026-09-12.md)：rc.14 / DELIVERY_LOCAL_CONTINUE_NOT_RUNNABLE，schema25保留旧数据；本地动作独立类型/选择起因，不伪造Pi命令。
> 64消息/50完整守卫，44帧/1288流轨迹/10摘要样例、104生成物；25项完整契约验证及82项相关组合通过，最终扩大回归1249通过/5跳过/1625子测试通过/1失败（既有Windows SQLite强杀恢复1546错误，未修复）。MCU连续轮次/逐轮累计已接，完整异常终态/清运/HMI/main及账本效果核对/恢复待接，未部署/烧录。下方为历史。

> 最新运行增量[P1Z](../hardware/docs/review/uart2-delivery-finalization-p1z-2026-09-12.md)：C选择窗口/按钮/超时、首次首末结果组装与Pi精确交接已本地验证；19项专项、92项相关回归通过。
> rc.13/schema24及104生成物不变；下一轮动作、HMI、完整业务/恢复/分类/main仍待接，Pi占用保持且不产生资金。未部署/烧录，下方为历史。

> 最新[P1Y投递选择精确交接](../hardware/docs/review/uart2-delivery-selection-custody-p1y-2026-09-12.md)：rc.13 / DELIVERY_SELECTION_CUSTODY_NOT_RUNNABLE，候选schema24；选择绑定原START/轮次/配置和已保存重量，实际C/Pi事务交接通过。
> 共63条消息、49条完整守卫，43帧/1242流轨迹/9摘要，104生成物；25项完整契约验证通过。选择产生/HMI、继续/结束/完整结果和正常main仍待接，不授权动作/清占用/资金，未部署/烧录。下方为历史。

> 最新运行增量[P1X关门后称重](../hardware/docs/review/uart2-delivery-postclose-p1x-2026-09-12.md)：实际CLOSE后按原配置等待、重新采样并经原START/轮次精确交接；10项新专项、152项相关组合通过。
> rc.12/schema23及104生成物不变；选择记录/完整结果、清运、恢复/main仍未接通，业务占用不提前释放。未部署/烧录，下方为历史。

> 最新运行增量[P1W首次投递执行](../hardware/docs/review/uart2-delivery-execution-p1w-2026-09-12.md)：真实C首次开门授权、双100ms断开和中断自动关门已接通，同原命令两条事件精确交接；Pi配置报文补入持久命令发送路径。
> 11项执行专项、53项定向组合通过；104生成物检查通过，rc.12/schema23不变。仅显式候选装配，完整业务/清运/恢复/main仍未接通，未部署/烧录。下方为历史。

> 最新[P1V动作记录精确交接](../hardware/docs/review/uart2-actuator-handoff-p1v-2026-09-12.md)：rc.12 / ACTUATOR_EVENT_HANDOFF_NOT_RUNNABLE，候选schema23。
> 新增查询/查询回复/精确保存/保存回复四条消息，共63条消息、48条完整守卫、42帧/1224流轨迹/8摘要（6种规则），104生成物三语言验证通过。
> 实际C→Pi事务保存→精确释放已接通；锁通断独立保存，断电不等待确认。报告关系仍需原账本核对，保存不推进业务/资金；动作执行与完整业务尚未接通，未部署/烧录。下方为历史。

> 最新核心增量[P1U共享事件编号额度](../hardware/docs/review/uart2-actuator-credits-p1u-2026-09-12.md)：实际控制入口同时保护记录空间和全局编号；216项相关测试、25项契约验证通过。
> rc.11/schema22/104生成物不变；尚无动作串口/Pi精确保存，下一步补原命令关系及交接后才能接动作。ROM33604/RAM5272仅探针，未部署/烧录。下方为历史。

> 最新运行核心增量[P1T动作记录预留](../hardware/docs/review/uart2-actuator-journal-p1t-2026-09-12.md)：C八条内存预留/保留/精确逐条释放已独立验证，45项模块及196项相关测试通过。
> rc.11/schema22和本目录104生成物不变；没有新的线级确认或Pi动作保存路径。全局事件编号额度、原业务关联、串口/Pi交接须先于实际动作接通。
> 核心ROM33124/RAM5272仅探针，未部署/烧录；原协议校验仍为下述P1S范围。下方保留历史。

> [P1S动作结果完整校验](../hardware/docs/review/uart2-actuator-events-p1s-2026-09-12.md)：rc.11 / ACTUATOR_EVENT_GUARDS_NOT_RUNNABLE，schema22不变。
> 59条消息中44条完整内容校验；38帧/1167流轨迹/5摘要三语言通过，104生成物；新增投递门/安全关门结果及清运锁供电变化校验。
> 动作可靠交接尚未接：原测量槽不能覆盖锁通断两条记录，断电不能等确认；需先有界多记录保留/查询/精确保存，再接实际执行。
> ROM32152/RAM4680仅核心链接探针；旧制品冻结，未部署/烧录，不改变OneNet或后端支持范围。下方为历史批次。

> [P1O过程精确交接](../hardware/docs/review/uart2-process-handoff-p1o-2026-09-12.md)：rc.10/schema22，实际C保留/原步骤查询/精确释放与Pi原文+关系同事务保存已实现。
> 59条消息，41条完整内容校验；35帧/1054流轨迹/5摘要，104生成物。确认不释放业务/最终结果或产生资金。
> 核心链接探针ROM27352/RAM4096字节，不是固件/完整栈证明；配置消费者/采集/业务/main/云端仍待接线，未部署/烧录。
> 旧制品冻结、旧后端清单/schema18仍拒绝候选；下方保留历史批次，当前无需用户操作。


> 最新[P1N](../hardware/docs/review/uart2-process-measurements-p1n-2026-09-12.md)：rc.9 / PROCESS_MEASUREMENTS_NOT_RUNNABLE，
> 六类过程称重统一新取值和证据，完整报文校验覆盖37条；31帧/978流轨迹/4摘要，104生成物。
> MCU四类业务事件组装、Pi原字节保存与冲突留档已实现；schema21候选仍被旧后端清单/schema18拒绝。
> 过程保留/精确确认、完整采集/业务/main/云端同步未接；旧制品冻结，未部署/烧录。下方为历史。

> 最新[P1M](../hardware/docs/review/uart2-config-runtime-budget-p1m-2026-09-12.md)：rc.8，C配置RAM切换/命令去重/互斥已验证；
> 104生成物含独立C共享校验实现，ARMCC需链接一次；三端31帧/730轨迹/4摘要及两种C链接方式通过，整机生效未接；
> schema20不变，旧制品冻结，不能发布/烧录。下方为历史记录。

> [P1H](../hardware/docs/review/uart2-command-guards-p1h-2026-09-12.md)更新为 `2.0.0-rc.6` / `COMMAND_GUARDS_NOT_RUNNABLE`：
> 25条消息完整内容校验，新增全9条COMMAND字段/摘要校验，三端566流轨迹通过。schema20不变。
> 不是完整业务执行/配置接线完成，不可发布；下方rc.5与schema19为历史增量，旧制品仍冻结。

> [P1G](../hardware/docs/review/uart2-control-wire-p1g-2026-09-12.md)：rc.5布局/schema20不变，实际C控制帧入口
> 和Pi结果接收/落盘/确认已联合验证；完整业务执行/主程序尚未接入，不可发布或烧录。下面为前批历史。

> 2026-09-12 [P1F运行候选增量](../hardware/docs/review/uart2-edge-dispatch-p1f-2026-09-12.md)：UART布局仍rc.5，
> Pi启动分配、命令只发一次/原命令查询与永久账本门已独立验证；EdgeStore递增到schema20。
> 正常主程序/MCU接收执行器未接入，后端仍只接受旧清单/schema18；下方schema19为前批结果交接历史。

- OneNet：Draft 2020-12 JSON Schema 已同步 rc.3 的配置、命令、可靠事件和运行快照；
  香橙派使用同一生成模型投影 OneJSON，F-10 已完成。
- UART：唯一可变 Registry 为原生 `2.0.0-rc.5` / `DEVICE_FACTS_NOT_RUNNABLE`
  （单投口设备事实候选，不能运行/发布）。共55条消息，累计16条新引导/会话消息三语言内容校验。
  自包含结果总帧213字节，不分片；MCU RAM保留/精确确认和真实EdgeStore结果+待上报任务
  同事务提交已验证。专用队列等待Pi分类，不直接发送到现有OneNet/资金链，不释放业务占用。
  原业务状态/结果引用查询与 MCU 记录组合、Pi 持久查询编号及单次只读发送已独立验证。
  218字节单帧设备事实查询已有实际C采集与Pi读取：门控事实同次刷新，原始读数时间、最后测量配置独立。
  这不是完整安全或多投口快照；schema19迁移保留数据，启动/动作发送、配置/采集与业务恢复接线仍待完成。
  [本批证据](../hardware/docs/review/uart2-device-facts-p1e-2026-09-12.md)。
- 既有 `hardware/uart_protocol.py` 和 `hardware_mcu/USER/uar/` 的 2 个生成制品保持
  原生 UART 1.0 原样，由 [`frozen-v1-runtime.json`](uart/frozen-v1-runtime.json) 检查摘要。
  这是不可变旧制品，不是第二份可变 Registry；旧源码可从 Git 历史及冻结 Python 内嵌
  Registry 追溯。运行入口仍显式使用 `fixed-frame` / `uart-v1`，不会导入 2.0 候选。
  新生成的 `hardware/uart2_protocol.py` 仅供独立候选API使用，进入包清单不代表启用新UART模式。
  后端仍仅接受旧文件集合/schema18；候选包不能直接远程更新，发布前需配套同步。
- 现有 MCU 固定帧：[`ecobin-mcu-fixed-frame-v2` 完整单文件协议](../hardware/docs/单片机-香橙派适配通信协议详细内容.md)
  `2.0.0` 在同一正文中逐字节定义全部 11 类业务、传感器、URL 和升级帧，以及解析、
  恢复和 revision 1 首次迁移边界；不再拆分 v1 基础帧与 F2/F3 扩展。
- HTTP：属于 F-09，不在 F-10 中创建。

F-10 完成不是生产切换授权。当前固定帧协议只允许在 F-11 的显式 `fixed-frame` 模式
使用，不能与 `uart-v1` 自动探测、同时双解析或失败回退；旧 D1 清运链必须退出。

## 目录

```text
contracts/
├─ onenet/
│  ├─ common.schema.json
│  ├─ event-envelope.schema.json
│  ├─ command-envelope.schema.json
│  ├─ command-receipt.schema.json
│  ├─ events/events.schema.json
│  ├─ commands/commands.schema.json
│  ├─ thing-model.mapping.yaml
│  └─ generated/
│     ├─ onenet-thing-model.candidate.json
│     ├─ onenet-wire-mapping.json
│     └─ java/
├─ uart/
│  ├─ uart-registry.schema.json
│  ├─ uart-registry.yaml
│  ├─ frozen-v1-runtime.json
│  ├─ mcu-review-checklist.md
│  └─ generated/
├─ examples/
│  ├─ onenet/
│  ├─ onenet-wire/
│  └─ uart/
├─ generated/
│  └─ contract-catalog.md
├─ tools/
└─ tests/
```

`*.yaml` 文件刻意使用 JSON 语法。JSON 是 YAML 1.2 的合法子集，这样 Python 3.11
标准库即可读取，无需在香橙派或 MCU 开发环境额外安装 YAML 解析器。

## 生成与验证

在仓库根目录运行：

```powershell
python contracts/tools/generate_contracts.py
python contracts/tools/validate_contracts.py
python -m unittest discover -s contracts/tests -v
```

生成物必须由机器源重建，不得直接编辑。检查工作区是否存在生成漂移：

```powershell
python contracts/tools/generate_contracts.py --check
```

生成器还会更新香橙派运行时使用的 `hardware/onenet_projection_model.json`，避免
运行时代码手写另一套枚举、nullable presence flag 或 OneNet 字段截断规则。

UART 默认仅生成 `contracts/uart/generated/`、对应样例和目录；不会覆盖旧运行文件。
每次生成/检查先核对 3 个冻结文件的摘要，发现漂移即失败，不能更新摘要来绕过。
`--include-hardware-mcu` 在候选阶段明确报错（包括 `--check`），不能再沿用旧文档导出
命令。待双方运行实现、保留数据迁移及成对发布审查完成，再显式解除冻结。

工具只使用 Python 3.11 标准库。Java 黄金样本由校验器在存在 Java 21 工具链时编译执行；
C 头文件和黄金样本程序在通用 C11 工具链验证；新增 14 条引导/会话消息的长度、数值及
身份/结果关系在交给流解析回调前校验。其余 39 条消息的完整 C/Java 内容执行器尚未完成。
候选流解析器对超过 512 字节的读取按剩余容量追加、立即排空，保留帧序，不扩容或先裁帧。
通用工具链通过不能替代 MCU 固件集成、资源预算或真机符合性证据。

完整的本地、OneNet 控制台、Java、C 与真机人工验证顺序见
[`MANUAL-VALIDATION.md`](MANUAL-VALIDATION.md)。
OneNet 控制台当前前端校验实现的原始快照、来源边界和候选文件兼容修复见
[`onenet-thing-model-frontend-validator.md`](../hardware/docs/onenet-thing-model-frontend-validator.md)；
它用于补强平台兼容检查，不替代本目录的权威机器契约。

尚未完成的三端完整 payload 执行器、跨消息状态轨迹、穷举负例，以及已修复的候选
长输入解析记录在
[`DEFERRED-HARDENING.md`](DEFERRED-HARDENING.md)。这些是 F-11/H-03 的实施要求，
不是允许运行时放宽 Registry。

## 摘要与单位

- JSON 稳定载荷只允许 `null`、布尔、字符串、整数、数组和对象；禁止 IEEE 754 浮点。
- `payloadSha256` 对 RFC 8785 可规范化的 `payload` 计算。当前工具对上述整数子集生成
  UTF-8、键排序、无多余空白的规范字节，并用黄金样本锁定。
- 稳定命令摘要使用 `ECOBIN:ONENET:COMMAND:v1\0` 域分离，绑定命令身份、类型、部署、
  目标、签发/截止时间和 `payloadSha256`；`cosGrant` 刷新不改变该摘要。
- 中心事件摘要使用 `ECOBIN:ONENET:EVENT:v1\0` 域分离，并额外绑定 OneNet 已认证的
  `productId + deviceName`，不能信任设备把来源身份写进业务载荷。
- UUID 使用 RFC 4122 文本；UART 中转换为网络顺序 16 字节。
- SHA-256 在 JSON 中使用 64 位小写十六进制，在 UART 中使用原始 32 字节。
- 重量使用带符号整数克；单价使用“元/千克 × 10000”的无符号整数；相对时长使用毫秒。
- `eventUid`、`commandUid`、`mcuCommandUid`、`txSequence`、`mcuEventSequence` 和
  OneNet/Pulsar 传输 ID 是不同身份，禁止互相替代。

## 修改规则

1. 先修改权威 Schema 或 Registry，再运行生成与验证。
2. 规范 UART 候选发生变更时必须重生成摘要和黄金样本；固定帧适配不得直接修改生成物
   或反向制造第二套 OneNet/业务契约。
3. 共同基线冻结后，破坏性变化提升 major，兼容新增提升 minor，纯说明/样例修正提升 patch。
4. 不得用机器契约反向削弱已确认的门安全、资金、幂等、租户/机构或失败恢复边界。
5. `onenet-thing-model.candidate.json` 是从 JSON Schema 生成的控制台导入候选；若控制台
   拒绝，记录原始错误并修改 Schema/生成器，不能直接改生成文件制造第二套契约。
