# EcoBin 文档中心

> 当前设备路线：[精简实施计划](planning/mcu-edge-simplified-implementation-plan-2026-09-13.md)，依据[简化恢复共识](architecture/mcu-edge-simplified-recovery-discussion-2026-09-13.md)。核心共识已收敛，进入新路线本地实施；旧复杂恢复不继续，现状代码与已验证完成范围分开记录。

> 当前增量：[控制通信失败收口](../hardware/docs/review/native-control-communication-failure-s2-2026-09-13.md)。本地检查点`3bdf7a11`之后已接实际原生Runtime：整链路或原START单独超过既有10秒期限时可靠结束原业务，迟到结果不恢复正常结算；未写START在Pi实例更换后明确开始前失败。相关统一151项通过。此前[MCU重启、配置重装和末重失败](../hardware/docs/review/native-reboot-and-weight-failure-s2-2026-09-13.md)保持。人工解除通信阻断、清运中断、S3断网释放及S4现场交付仍未完成，不能部署烧录。下方复杂恢复候选均为历史。

> 最新候选 [P1BT：已授权未登记发送的恢复关门撤回](../hardware/docs/review/uart2-recovery-close-withdrawal-p1bt-2026-09-13.md)：业务库先封住旧发送，永久层另存撤回事实并保留原授权历史；独立继任仅豁免准确祖先，候选循环已接自动核对，不发新动作、不启动云端、不恢复接单，业务39/永久3不变。
> 撤回专项53项与运行入口19项分别通过，最终集成/扩大回归见实施记录，完整契约25通过。已登记可能发送的未知效果、跨新启动号新动作、完整准入/云端/正常业务与main切换仍待接，P4/P5未完成。
> 未部署/烧录；OneNet2.2.0/UART rc.22/MySQL V79/权限40及发布25/39门槛不变。外部刷写/HIL不保证共用串口锁；RS485/HMI/完整固件容量保留。两项投递取舍已确认，下方历史“待确认”不再适用。

> 最新候选 [P1BH：后台确认可靠交接](../hardware/docs/review/uart2-native-confirmation-p1bh-2026-09-13.md)：原始确认、报告与回执原子绑定，schema32；322项相关测试、25项完整契约检查通过。
> 投递重启缺最终包只归档、归档后迟到结果只追加证据，两项已获用户确认，不再待裁决；实现继续按P4/P5推进。确认交接不释放占用、不修改当前袋或准入，main尚未切换。
> 未部署/烧录；发布25/32、RS485与HMI等剩余门槛保留。以下为历史记录，历史“P4待确认”不再适用于上述两项决定。

> 最新硬件候选 [P1BG：原生上报与编号落库](../hardware/docs/review/uart2-native-report-persistence-p1bg-2026-09-13.md)：实际 C 多轮/5 秒中位数/末重超时及照片快照已接可靠报告；缺测不补零，清运量为本次前重减后重。
> V78 只对齐四个业务测量编号，原质量/范围/空值约束保留；启动/供应目标同步，134 表/授权39不变。真实 MySQL 297 项、相关 Java 总计399项、Pi/隔离包50项、完整契约25项通过。
> 未部署/烧录；主程序、后端确认消费、当前袋/准入与完整异常仍待接。发布25/31、RS485、完整固件容量及 P4/HMI 待确认项保留，P3/P5/P7未整体完成。下方为历史。

> 最新：[P1BF原生业务上报与清运前后差值](../hardware/docs/review/uart2-business-report-p1bf-2026-09-13.md)。用户确认清运前减清运后；Pi/后端/合同同步。候选schema31/OneNet2.1.3，119项设备侧、85项Java、246项/1214子用例回归和25项合同校验通过；未部署，完整main/袋转换/确认消费仍待接，发布25/31门槛保留。下方为历史。

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

> 最新硬件[P1AI清运解锁前称重失败](../hardware/docs/review/uart2-clean-preunlock-failure-p1ai-2026-09-12.md)：首重无结果/中断精确保存后冻结FAILED；零解锁步骤、末重未采集、不伪造人工确认，Pi重启/丢回执不重放、不清占用，上一单数据不串单。
> rc.19/schema27不变，300项相关及25项完整契约验证通过；扩大回归1668通过/5跳过/1635子测试通过/零失败，ROM50548/RAM6064仅核心探针，历史SQLite偶发1546未宣称修复。已解锁异常/账本/恢复/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，当前无需用户操作。下方为历史。

> 最新硬件[P1AH清运人工确认与结果交接](../hardware/docs/review/uart2-clean-confirmation-p1ah-2026-09-12.md)：当前末重与人工关门确认分别精确保存后冻结结果；重开锁不复用旧重量，Pi重启/丢确认不重放、不清占用，中断结果保留失败。
> rc.19/schema27，176项相关及25项完整契约验证通过；扩大回归1631通过/5跳过/1635子测试通过/1失败（既有Windows SQLite强杀恢复1546，未修复），ROM50196/RAM6064仅核心探针。完整清运异常/账本/恢复/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，当前无需用户操作。下方为历史。

> 最新硬件[P1AG清运意图与独立末重](../hardware/docs/review/uart2-clean-intent-p1ag-2026-09-12.md)：原按钮请求精确保存后再次单次开锁/独立称重；重新开锁作废旧候选但保留原文，Pi重启/丢确认不重放，不清业务占用。
> rc.18/schema26，126项相关及25项完整契约验证通过；扩大回归1533通过/5跳过/1635子测试通过/零失败，ROM48488/RAM6048仅核心探针。人工关门确认/最终结果、完整异常/账本/恢复/分类/HMI/main仍待接，P3未整体完成；未部署/烧录，无需当前用户操作。下方为历史。

最新硬件：[P1AF首次清运开锁](../hardware/docs/review/uart2-clean-first-unlock-p1af-2026-09-12.md)。原命令单次通电/独立定时断电、两份记录及Pi重启/丢确认交接已验证，不推断门位或清Pi占用。27项专项、25项契约检查通过，扩大回归见记录；rc.17/schema25不变。再次开锁/完成/清运末重/HMI、账本/恢复/main待接，未部署/烧录，无需用户操作。下方为历史。

最新硬件：[P1AE关门后中断交接](../hardware/docs/review/uart2-postclose-interruption-p1ae-2026-09-12.md)。实际C自动检测更新停止/关门控制上下文丢失，原输出/测量/选择保存后失败收尾，重启不重放、不清占用；rc.17/schema25，192项相关及25项契约检查通过，扩大回归1412通过/5跳过/1635子测试通过/零失败。清运/HMI、完整异常/账本/恢复/分类/main待接，未部署/烧录，无需用户操作。下方为历史。

最新硬件：[P1AD测量中断交接](../hardware/docs/review/uart2-interrupted-measurement-p1ad-2026-09-12.md)。中断与秤超时/未采集分开，实际C/Pi精确保存后失败收尾，重启不重放、不清占用；rc.16/schema25，176项定向及25项完整契约检查通过，扩大回归与静态告警见记录。自动关门后异常触发/清运/HMI/账本/恢复/main待接，未部署/烧录，无需用户操作。下方为历史。

最新硬件：[P1AC动作中断交接](../hardware/docs/review/uart2-delivery-abort-p1ac-2026-09-12.md)。CLOSE前中断有原输出/独立原因及精确保存后的失败结果，不补造关门或末重、不清Pi占用；rc.15/schema25，234项定向及25项完整契约检查通过，扩大回归见记录。完整异常/清运/HMI/账本/恢复/main仍待接，未部署/烧录，无需用户操作。下方为历史。

最新硬件：[P1AB首次称重失败收尾](../hardware/docs/review/uart2-delivery-preopen-failure-p1ab-2026-09-12.md)。首重失败精确保存后冻结原失败结果，不开门、不补造末重、不提前清Pi占用；新增11项专项、140项相关回归通过。rc.14/schema25不变，扩大回归与Windows SQLite取证状态见记录；完整异常/清运/HMI/恢复/main仍待接，未部署/烧录，无需用户操作。下方为历史。

最新硬件：[P1AA原会话内连续投递](../hardware/docs/review/uart2-delivery-local-continue-p1aa-2026-09-12.md)。实际MCU继续轮次/逐轮异常累计及本地动作精确交接已验证；rc.14/schema25保留旧数据，82项相关组合、25项契约验证通过。最终扩大回归1249通过/5跳过/1625子测试通过/1失败（既有Windows SQLite强杀恢复1546错误，未修复）；未接完整main/HMI/清运/恢复，未部署/烧录，无需当前用户操作。下方硬件段落为历史。

最新硬件：[P1Z选择窗口与首次投递最终结果](../hardware/docs/review/uart2-delivery-finalization-p1z-2026-09-12.md)。实际C按原配置产生选择、精确交接后组装原首末结果，Pi重启/丢确认只查询原文；19项专项、92项相关回归通过，扩大回归1152通过/34跳过/1624子测试通过，零失败。
rc.13/schema24不变；下一轮动作、HMI、清运、账本/恢复/分类/main仍未接完。未部署/烧录，无需用户操作，下方硬件进展为历史。

最新硬件：[P1Y投递选择精确交接](../hardware/docs/review/uart2-delivery-selection-custody-p1y-2026-09-12.md)。选择关联原轮次已保存重量，实际C与Pi事务交接通过；rc.13/schema24，52项定向与25项完整契约验证通过；最终扩大回归1133通过/34跳过/1624子测试通过，零失败，SQLite历史问题未宣称修复。
选择产生/HMI、继续/结束与最终结果、清运、账本恢复和main未完成；未部署/烧录，无需当前用户操作。下方硬件进展为历史。

全局满溢与租户权限：[实施与验证](planning/global-fullness-and-tenant-device-config-2026-09-12.md)已提交并部署为 `20260912094429-2d6c6c938e1c`。V70 与完整授权检查通过，统一 50 千克或红外满足，13 台已有配置、1 台已应用/12 台待应用；[部署记录](operations/global-fullness-deployment-2026-09-12.md)。

设备列表：[显示调整计划](planning/device-list-information-plan-2026-09-12.md)已提交并部署为 `20260912083501-6c8c4e2e91e1`，12 项生产构建页面检查和公网 77 个文件校验通过；见[部署记录](operations/device-list-deployment-2026-09-12.md)。

这里是 EcoBin 的文档入口。新成员或新的 AI 协作者建议先读“快速开始”，再按任务进入对应专题。

## 快速开始

设备详情页：[信息分层调整与验证](planning/device-detail-information-plan-2026-09-12.md)已提交并部署为 `20260912073703-8800b3d17c6f`，生产构建 10 项设备检查与公网 76 个文件校验通过；见[部署记录](operations/device-detail-deployment-2026-09-12.md)。

最新：[P1X关门后称重](../hardware/docs/review/uart2-delivery-postclose-p1x-2026-09-12.md)。按原配置等待后建立独立测量窗口，沿原START/轮次精确交接；10项专项、152项相关组合通过。
扩大回归1082通过/34跳过/1624子测试通过，零失败；SQLite历史偶发问题未宣称修复。
仍保持原业务占用，选择/最终结果、清运、账本恢复及main未完成。rc.12/schema23不变，未部署/烧录，当前无需用户操作。下方为历史。

[P1W首次投递执行](../hardware/docs/review/uart2-delivery-execution-p1w-2026-09-12.md)。原命令只执行一次、100ms双断开和中断自动关门，已与Pi真实发送/永久账本/SQLite交接联合验证。
扩大回归1072通过/34跳过/1624子测试通过，零失败；最后加固另有144项最终定向验证。SQLite历史偶发问题仍保留追踪。
11项执行专项、53项定向组合通过；完整业务、清运、恢复及正常main尚未接通，未部署/烧录。rc.12/schema23不变；既有SQLite强杀恢复问题继续追踪，下方为历史。

[P1V动作记录精确交接](../hardware/docs/review/uart2-actuator-handoff-p1v-2026-09-12.md)。实际C串口查询→Pi事务保存→精确确认已本地接通；锁通断分别保存，断电不等待确认。
扩大回归1059通过/34跳过/1624子测试通过/1失败：既有SQLite强杀恢复I/O问题独立复现，尚未修复；具体错误及样本检查已归档。
rc.12/schema23，117项相关测试及25项契约验证通过。保存不推进业务或产生资金；原命令关联/单次执行/自动关门仍待接，未部署/烧录，无需用户操作。下方为历史。

[P1U共享事件编号额度](../hardware/docs/review/uart2-actuator-credits-p1u-2026-09-12.md)。控制入口同时预留记录空间与编号额度，称重不能占用断电预留；216项相关测试通过。
下一步原命令关系、串口/Pi精确交接后接动作；rc.11/schema22，未部署/烧录，无需当前用户操作。下方为历史。

[P1T动作记录预留核心](../hardware/docs/review/uart2-actuator-journal-p1t-2026-09-12.md)。MCU八条内存记录可先预留锁通断位置、保留原文并逐条确认释放；45项模块及196项相关回归通过。
尚未接全局事件编号额度、串口查询/确认、Pi存储或实际动作；不是可靠交接全链路完成。rc.11/schema22不变，未部署/烧录，无需当前用户操作。

[P1S动作结果完整校验](../hardware/docs/review/uart2-actuator-events-p1s-2026-09-12.md)。投递门、安全关门、清运锁供电三类消息已完整校验；rc.11/schema22。
最终154项局部回归和三语言38帧/1167轨迹/5摘要通过；仍需动作多记录保留/精确交接后再接实际执行，不允许等保存确认才断电。未部署/烧录，无需当前用户操作。

[P1R首次开门前置核对](../hardware/docs/review/uart2-opening-gate-p1r-2026-09-12.md)。原业务/配置/重量精确保存与原期限已接入只读核对，含首次清运解锁。
不受理、不动作；机械事件交接、换向等待、单次输出/自动关门和再次开锁仍待接。rc.10/schema22，未部署/烧录，无需当前用户操作。

[P1Q业务准备](../hardware/docs/review/uart2-work-preparation-p1q-2026-09-12.md)。实际字节流配置/开始业务到前置称重、首份过程记录和SQLite精确交接已本地验证；
不直接开门，配置未实际应用拒绝开始；原指令/受理时间重连后保持，旧重量不因延迟发布变新。37项准备测试通过。
完整配置消费者/授权解锁/业务/main/真实采集/云端仍待接；rc.10/schema22，未部署/烧录。下方保留历史。

[P1P配置驱动测量](../hardware/docs/review/uart2-weight-run-p1p-2026-09-12.md)。实际C配置/原始回复/5秒取值到过程保留与SQLite交接已本地验证；
真实RS485归属、完整业务/main/全机配置应用仍未接通。rc.10/schema22不变，未部署/烧录；核心探针ROM28140/RAM4184不是固件。

最新：[P1O过程记录精确交接](../hardware/docs/review/uart2-process-handoff-p1o-2026-09-12.md)。C保留原文并按原步骤返回，Pi原文与业务关系一起落盘后确认。
rc.10/schema22；丢确认和Pi重启不重称、不重放动作；确认不结束业务、不产生资金。仍未接完整业务/main或部署/烧录。


最新：[P1N过程称重与本机证据](../hardware/docs/review/uart2-process-measurements-p1n-2026-09-12.md)。六类过程称重统一均值/中位数/不可用，
C四类业务事件组装与Pi原字节保存已实现；rc.9/schema21。保存不自动ACK或产生资金，业务/采集/主程序和云端同步未完成，未部署/烧录。

最新：[P1M配置运行与容量修正](../hardware/docs/review/uart2-config-runtime-budget-p1m-2026-09-12.md)。配置RAM切换/
去重/互斥已验证；共享校验解决核心64KB超限，只链接探针ROM21192字节。实际消费者/业务/云端同步未完成，不可烧录。

Web 页面调整：[信息减量与操作提示计划及实现记录](planning/web-contextual-guidance-plan-2026-09-12.md)。21 个业务页面已上线，发布为 `20260912064344-686a7febbfce`，公网验证通过；详见[部署证据](operations/web-contextual-guidance-deployment-2026-09-12.md)。

最新：[P1H业务命令校验](../hardware/docs/review/uart2-command-guards-p1h-2026-09-12.md)。rc.6候选，9条业务命令完整字段/
摘要及三端566条流轨迹通过；还不是业务执行完成，不可部署/烧录。继续MCU结果组装与配置/采集接线。

最新：[P1G实际控制报文与结果接收](../hardware/docs/review/uart2-control-wire-p1g-2026-09-12.md)。
MCU实际字节流控制入口与Pi落盘后精确确认已本地验证；未接业务执行/main/Keil，不释放业务或产生资金。
继续完整命令校验与业务接线，仍不部署/烧录。下方保留历史。

最新：[Pi启动与单次发送 P1F](../hardware/docs/review/uart2-edge-dispatch-p1f-2026-09-12.md)。schema20保留数据增加
启动/命令记录，永久账本与只发一次/丢回复查原命令已联合验证；仍为未接生产的原生候选，未部署/烧录。
用户已授权自主继续，不逐批询问；接着做实际MCU控制入口与业务接线。以下保留此前批次记录。

当前硬件计划入口：[MCU—香橙派契约对齐与分批实施计划](planning/mcu-edge-contract-implementation-plan-2026-09-12.md)。
已获开始实施授权；[首批实现与测试记录](../hardware/docs/review/mcu-core-first-batch-2026-09-12.md)
记录 MCU 门控/计时/解析接线与新称重核心独立验证。整体尚未完成，未部署或烧录。
此前已完成[启动绑定/命令/结果 P0 条件性模型](../hardware/docs/review/mcu-session-p0-model-2026-09-12.md)。
继[身份契约 P1A](../hardware/docs/review/uart2-identity-contract-p1a-2026-09-12.md)之后，已完成
[完整结果交接 P1C](../hardware/docs/review/uart2-result-handoff-p1c-2026-09-12.md)：213字节完整结果、
MCU保留/精确确认、真实SQLite结果与待上报任务原子提交已验证。Registry为2.0.0-rc.3、schema19，
仍不可运行；专用任务等待业务分类，主程序/单次发送/恢复接线待完成。旧运行文件冻结，未部署，
不需要本批烧录或HMI操作。[P1B](../hardware/docs/review/uart2-session-foundation-p1b-2026-09-12.md)保留前批证据。
此前[原业务查询 P1D](../hardware/docs/review/uart2-work-query-p1d-2026-09-12.md)已实现原业务状态/结果引用查询，
MCU业务记录与结果槽组合、Pi持久查询编号及单次只读发送，含实际C/Python/SQLite投递清运交接测试。
Registry为2.0.0-rc.4、schema19；一致设备事实快照和运行接线未完成，后端旧发布规则仍拒绝候选包。
最新[单投口设备事实 P1E](../hardware/docs/review/uart2-device-facts-p1e-2026-09-12.md)补同次控制刷新的门目标/PB5/输出，
及原始重量、最后测量和配置的独立时间/版本，实际C与Pi只读查询已验证；Registry为2.0.0-rc.5。
仍未接新查询主流程、不替代完整安全检查、不清业务占用或产生资金；未部署/烧录，下一步补启动/动作编号及发送。
分工与共识背景见[总体方案](architecture/mcu-edge-refactor-plan-2026-09-11.md)，未决事项不因计划完成而自动确认。

1. [项目上下文](architecture/project-context.md)：当前阶段、跨会话决策、已知工程坑和续作入口。
2. [V52 设备自注册、厂家初始袋与按需远程维护](architecture/device-enrollment-factory-acceptance-remote-support-v52.md)：全局厂家引导密钥的一次性清理、OneNet 自动创建设备、验收前真实装袋、管理员公钥一次登记及四端口按需反向 SSH。
3. [V52 设备出厂与远程维护代码审查](review/device-enrollment-factory-remote-support-v52-review-2026-08-15.md)：跟踪厂家袋扫码、验收证据代次、远程租约与生产证书权限的 4 项 P1、3 项 P2 及修复证据。
4. [V53 投递自动审核与自动提现](architecture/delivery-auto-review-and-withdrawal-v53.md)：正常投递的版本化自动审核、异常转人工、首次正返现自动提现及安全跳过边界。
5. [V54 投递审核金额阈值与 Web 配置中心](architecture/delivery-review-amount-limit-and-configuration-center-v54.md)：按结算金额决定自动或人工审核，复用单次最大提现金额，并收拢两类机构业务规则入口。
6. [香橙派可重复量产镜像与首次启动编排计划](planning/orangepi-production-image-first-boot-plan.md)：统一面向 32 GB TF 卡的 Debian 12/Python 3.11 镜像构建、UART5、可选 BOOT0/NRST 升级能力、Air780E USB RNDIS 上行、隔离验收热点、500 g±10 g 称重预检、真实或明确标记的 MCU 模拟外设证据、K1 清理、单向封存、revision 2、双摄预检和镜像发布物；真实 HIL 门禁仍关闭。
7. [V36 设备永久归属、自动验收与无部署码模型](architecture/permanent-device-ownership-v36.md)：设备全链路目标，覆盖旧部署、调拨和人工激活模型；2026-08-10 补充未分配设备运行、故障与安全事实的平台作用域。
8. [V41 全局固定设备二维码入口](architecture/global-miniapp-device-entry-v41.md)：普通二维码统一使用全局地址，设备公开码区分设备和机构，渠道/机构不再保存入口地址。
9. [V42 设备入口 URL 下发](architecture/device-entry-url-edge-delivery-v42.md)：验收时保存完整 URL，全局地址改变时自动下发，MCU 无应答且屏幕结果不进入平台验收。
10. [V43 平台防伪袋码与标签打印](architecture/authenticated-bag-labels-v43.md)：平台按批签发 EB1 标签，不预建库存；厂家登记和清运换袋时由后端验真。
11. [V46 设备运行快照全局策略](architecture/runtime-snapshot-reporting-v46.md)：状态变化最多每 5 秒合并，空闲默认每 60 分钟兜底，平台全局配置且不清理历史事实。
12. [V49 最近登录机构账号选择](architecture/recent-miniapp-organization-account-v49.md)：多机构用户需要新会话时进入最近成功登录的可用账号，设备扫码仍优先。
13. [V50 平台管理员引导与治理](architecture/platform-administrator-governance-v50.md)：空管理员表创建受保护的默认账号；只有默认管理员能治理其他平台管理员，普通管理员只能自行改密。
14. [应用修改后重新部署操作手册](deployment/application-redeployment-runbook.md)：代码提交后本地构建 JAR/dist、上传服务器、制作运行镜像、预检、激活、验证和回退的日常入口。
15. [V25 设备上报当前袋满溢状态](architecture/fullness-reporting-v25.md)：覆盖旧主动检测方案；只有当前袋明确 `FULL` 阻止下一次投递。
16. [设备接入、配置恢复、投递与清运一致性审查](architecture/device-delivery-clean-generation-consistency-review-2026-08-10.md)：说明厂家袋、当前袋、容量和重量基准之间的不变量，以及配置失败后的新版本恢复边界，并跟踪本轮逐项修复证据。
17. [投递全链路联调复盘与复跑手册](operations/delivery-e2e-integration-retrospective-2026-08-02.md)：真实 OneNet/COS、模拟 MCU/双摄的历史联调复盘；自 V38 起模拟来源不再阻止平台机器验收，真实物理质量仍由厂家质检和 H-03 验证。
18. [产品需求基线](planning/requirements-baseline.md)：当前目标需求；与旧实现冲突时用于判断后续应实现什么。
19. [一周 P0 范围基线](planning/p0-scope-baseline.md)：近期受控真实闭环的承诺范围和验收边界。
20. [P0 业务模型基线](planning/business-model-baseline.md)：已冻结的业务主体、事实、状态机和不变量；设备章节由 V36 专题覆盖。
21. [P0 系统架构设计基线](planning/system-architecture-draft.md)：已冻结的系统边界、当前八模块布局、一致性、安全、运行与验证方案。
22. [P0 目标数据库设计基线](planning/database-design-draft.md)：已确认的目标表族、约束、事务、并发和迁移方案。
23. [P0 目标接口设计基线](planning/interface-design-draft.md)：已冻结的 Web、小程序、OneNet、微信、UART、模块公开端口和机器契约。
24. [P0 详细设计与任务拆分](planning/detailed-design-draft.md)：已批准的施工方案、跨端责任、任务依赖与目标窗口。
25. [P0 受控闭环正式任务](planning/tasks/p0-controlled-loop/00-index.md)：历史任务入口；设备相关旧部署步骤由 V36 裁决覆盖。
26. [权限与角色设计](architecture/permission-design.md)：三类登录主体、多租户隔离和接口鉴权。
27. [旧运行数据库设计](architecture/database-design.md)：V1～V14 旧实现的表结构；新栈目标迁移已推进到 V63，并由只读 epoch guard 校验，运行制品不携带 Flyway 或迁移脚本。

## 目录说明

### `architecture/` — 架构与领域设计

- [项目上下文](architecture/project-context.md)
- [单片机与香橙派最小事实逐项共识](architecture/mcu-edge-minimal-facts-consensus-2026-09-11.md)：讨论中、未整体实施；已确认启动回复、编号关联、防重复动作、结果保存确认和按编号查询。门状态保留最近有效方向（01=关、10=开）；PB5 仅处理关门防夹，不单独判故障或阻止其他条件已满足后的下一笔开门。投递失败后香橙派自动关门并检查；清运缺失数据留异常，由原清运员完成确认后核对袋子、建立新空袋基准。恢复由香橙派本机负责，后端不指挥，但开始时已有操作登记，最终依据上报应用实际结果；旧重启中止及异常结果接收需要适配。准确度交人工；已确认 250 毫秒目标读取间隔、最近 5 次最大最小差不超过 100 克取均值，最多等 5 秒，纯波动超时取中位数继续业务，无可用数据才走测量失败。需解耦清运锁计时并同步跨端规则，现场响应速度及具体协议仍待验证/设计。
- [香橙派业务程序发布与远程更新设计](architecture/orangepi-business-runtime-release-and-update-design.md)：区分出厂程序、可替换业务程序和永久设备管理层，记录九阶段迁移顺序；第三阶段已在 v13 完成受控在线验收，第四阶段默认关闭基础已在 v19 完成指定单卡接入和普通业务真机验收；v23 已完成写卡、接入、封存、投递和清运，并在第一次所有权试切换发现问题后安全恢复；持久切换修复现已进入 v24 无秘密/HIL 候选并通过两套离线审计，尚待加入允许列表、写卡和真机更新/回滚/断电恢复，远程下发继续关闭。
- [V52 设备自注册、厂家初始袋与按需远程维护](architecture/device-enrollment-factory-acceptance-remote-support-v52.md)
- [V52 设备出厂与远程维护代码审查](review/device-enrollment-factory-remote-support-v52-review-2026-08-15.md)
- [V53 投递自动审核与自动提现](architecture/delivery-auto-review-and-withdrawal-v53.md)
- [V54 投递审核金额阈值与 Web 配置中心](architecture/delivery-review-amount-limit-and-configuration-center-v54.md)
- [V25 设备上报当前袋满溢状态](architecture/fullness-reporting-v25.md)
- [V41 全局固定设备二维码入口](architecture/global-miniapp-device-entry-v41.md)
- [V42 设备入口 URL 下发](architecture/device-entry-url-edge-delivery-v42.md)
- [V43 平台防伪袋码与标签打印](architecture/authenticated-bag-labels-v43.md)
- [V46 设备运行快照全局策略](architecture/runtime-snapshot-reporting-v46.md)
- [V49 最近登录机构账号选择](architecture/recent-miniapp-organization-account-v49.md)
- [V50 平台管理员引导与治理](architecture/platform-administrator-governance-v50.md)
- [设备接入、配置恢复、投递与清运一致性审查](architecture/device-delivery-clean-generation-consistency-review-2026-08-10.md)
- [F-01 九模块骨架与过渡退出清单](architecture/f-01-module-transition-inventory.md)
- [F-02 identity 边界与可信上下文实施证据](architecture/f-02-identity-boundary-evidence.md)
- [F-03 业务模块边界搬迁实施证据](architecture/f-03-business-boundary-evidence.md)
- [F-09 HTTP OpenAPI 与客户端传输基础实施证据](architecture/f-09-http-client-transport-evidence.md)
- [V-01 身份与 Web 管理纵切实施证据](architecture/v-01-identity-web-slice-evidence.md)
- [V-02 机构用户注册、手机号、钱包与管理绑定软件证据](architecture/v-02-organization-user-registration-wallet-evidence.md)
- [权限与角色设计](architecture/permission-design.md)
- [数据库设计](architecture/database-design.md)

### `api/` — 当前旧接口与调试资产

- [当前前端对接接口](api/api-frontend.md)：用于旧实现联调，不是目标接口设计基线。
- [Postman 集合](api/EcoBin.postman_collection.json)

### `frontend/` — 当前客户端接入状态

- [Web 管理端能力地图](frontend/web-capability-map.md)：记录当前 `/api/v1` 页面、能力组合、后续业务切片和交付门槛。

### `iot/` — OneNet 与设备云端集成

- [OneNet 物模型说明](iot/onenet-thing-model.md)：当前旧实现资料；目标契约以 `planning/interface-design/` 为准。
- [OneNet 物模型 JSON](iot/onenet-thing-model.json)：当前控制台结构，不是尚未实施的目标机器契约。
- [MQTTX 设备模拟](iot/onenet-device-simulation.md)

### `deployment/` — 部署与运维

- [投递异常隔离收口部署与新卡账本启用](operations/delivery-quarantine-deployment-2026-09-11.md)：2026-09-11 OneNet 18/22、V69 和后端/Web 已上线；经确认完成 v37 保留数据切换、仅账本模式修正和永久激活，更新器重启保持、后台兼容接入正常。未执行异常收口或机构动作，新 F3 `0x0B` 尚不满足收口安全要求。
- [香橙派量产镜像、写卡与整机验收手册](deployment/orangepi-production-image-factory-runbook.md)：从输入锁、两次构建、受控注密、签名发布和写卡复读，到离线硬件验收、Air780E 注册、云端授权、单向封存、冷启动放行与返工边界；当前真实 HIL 和 32 GB 布局锁未完成，不得量产放行。
- [v37 自定义重量报告校验镜像证据](../hardware/image-artifacts/evidence/hil-weight-report-validation-20260910-37/README.md)：2026-09-10 已将跨环节重量校验修复制成镜像，2180 项自动化测试及镜像内 ARM64 完整报告测试通过，指定 TF 卡完整写入/回读摘要一致。断电后验证已完成软件包并复用缓存，只重做中断的镜像组装；随后补齐后台认可配置，设备新报告已通过云端机器验收，实物称重精度仍未校准。
- [v37 后端认可版本部署与现场复验](operations/orangepi-v37-backend-allowlist-deployment-2026-09-10.md)：用户确认后只追加 v37 并重载既有应用，备份及独立健康/配置核验通过；21:44 新报告通过、原失败记录保留，设备已确认封存授权。不部署 V69、OneNet 新模型或后端代码。
- [v36 称重标准与采样展示镜像证据](../hardware/image-artifacts/evidence/hil-weight-reference-20260910-36/README.md)：2026-09-10 已完成镜像、2121 项自动化测试、ARM64 离线执行及指定 TF 卡完整写入/回读，摘要一致；保留并验证下载缓存复用。用户随后冷启动发现非 500 克报告被下游拒绝，该软件缺陷已在 v37 修复并写卡；真实 500 克测得约 28 克的精度问题仍未解决。后续诊断见[称重报告校验记录](../hardware/docs/review/factory-weight-reference-and-measurements-2026-09-10.md)。
- [v35 投递异常隔离镜像证据](../hardware/image-artifacts/evidence/hil-delivery-recovery-quarantine-20260910-35/README.md)：2026-09-10 已完成本地镜像、2080 项自动化测试及 ARM64 离线执行，并向用户确认的新卡写入与完整回读，摘要一致。用户随后反馈热点 500 克验收不通过；旧身份与账本不迁移，服务器旧记录未清理，配套部署与真机异常隔离验收仍未完成。
- [设备自注册、厂家验收与按需反向 SSH 部署手册](deployment/device-enrollment-and-remote-support-rollout.md)：V52 数据迁移、服务器 SSH 边界、生产秘密、香橙派注册包、试点验收和功能回退。
- [应用修改后重新部署操作手册](deployment/application-redeployment-runbook.md)：日常代码发布的范围判断、构建、上传、安装、预检、激活、验证与回退步骤。
- [生产部署配置、密钥与证书清单](deployment/production-configuration-secrets-certificates.md)：逐项说明服务器配置文件、秘密、微信支付公钥/商户证书、机构小程序配置和启动前验收。
- [目标单机部署手册](deployment/target-single-host-deployment.md)：当前 `115.159.67.35` 目标栈的安装、启动、Nginx 切换与回退步骤。
- [后端 Docker 部署](deployment/deploy-docker.md)
- [Web 管理端 Docker 部署](deployment/deploy-web-docker.md)

### `operations/` — 操作与恢复证据

- [H-01 旧栈恢复单元与所有权证据](operations/h-01-legacy-recovery-evidence.md)
- [H-02 目标数据库供应证据](operations/h-02-target-database-evidence.md)：包含 2026-08-29
  腾讯云 CDB V60 空库、初始超级管理员、账号级 TLS、未切换旧 V59 后端和待迁移 COS 的
  当前接力状态。
- [投递全链路联调复盘与复跑手册](operations/delivery-e2e-integration-retrospective-2026-08-02.md)：V25 真实云链路、模拟 MCU/双摄的故障分层、复跑顺序和证据清单。
- [香橙派 v17 后端允许列表部署记录](operations/orangepi-v17-backend-allowlist-deployment-2026-09-03.md)：记录 v17 加入、未写卡 v15/v16 移除、可恢复配置备份以及生产重载和健康核验结果，不包含运行秘密。
- [香橙派 v19 后端允许列表部署记录](operations/orangepi-v19-backend-allowlist-deployment-2026-09-03.md)：记录第四阶段镜像身份替换 v17、v15/v16/v18 继续排除、并发安全回退、可恢复配置备份以及生产重载和健康核验结果；允许版本不代表已经写卡或通过真机验收。
- [香橙派 v20 后端允许列表部署记录](operations/orangepi-v20-backend-allowlist-deployment-2026-09-03.md)：记录曾在保留 v19 的同时追加 v20 的历史部署；v20 从未写卡，随后已由 v21 替换。
- [香橙派 v21 后端允许列表部署记录](operations/orangepi-v21-backend-allowlist-deployment-2026-09-03.md)：记录允许列表从 v19+v20 切换为 v19+v21、可恢复配置备份、生产重载和独立健康核验；允许版本不代表真实 MCU 更新已通过或远程更新已开放。
- [香橙派 v23 后端允许列表部署记录](operations/orangepi-v23-backend-allowlist-deployment-2026-09-04.md)：记录 v23 指定 TF 卡完整写入回读、在保留 v13/v19/v21 的同时加入后端允许列表、可恢复配置备份、生产重载和独立健康核验；允许版本不代表本地业务更新真机验收或远程下发已经通过。

### `flows/` — 流程图与可视化说明

- [投递流程交互图](flows/delivery-flow.html)：旧实现/历史交互图；目标投递以详细设计第 04 章为准。
- [照片上传流程交互图](flows/photo-upload-flow.html)
- [用户投递流程图](flows/用户投递流程图.jpg)
- [清运流程图](flows/清运流程图.jpg)

### `planning/` — 需求、计划与待办

- [香橙派可重复量产镜像与首次启动编排计划](planning/orangepi-production-image-first-boot-plan.md)：32 GB TF 卡的 Debian 12/Python 3.11 量产镜像、可选 MCU 远程升级线与能力事实、Air780E RNDIS 上行、固定离线验收热点、出厂 MCU 外设模拟、云端验收后单向封存、首启恢复和无需 SSH 出厂验收；真机门禁保持关闭。
- [产品需求基线](planning/requirements-baseline.md)
- [一周 P0 范围基线](planning/p0-scope-baseline.md)
- [P0 业务模型基线](planning/business-model-baseline.md)
- [P0 系统架构设计基线](planning/system-architecture-draft.md)
- [P0 目标数据库设计基线](planning/database-design-draft.md)
- [D-046 微信免确认收款授权数据库设计](planning/database-design/10-merchant-transfer-authorization-d046.md)
- [F-04 V1～V4 数据库验证矩阵](planning/database-design/f-04-v1-v4-verification-matrix.md)
- [F-05 V5 recycling 验证矩阵](planning/database-design/f-05-v5-recycling-verification-matrix.md)
- [F-06 V6～V10 funds/operations 验证矩阵](planning/database-design/f-06-v6-v10-funds-operations-verification-matrix.md)
- [F-07 epoch guard/Fake bootstrap 验证矩阵](planning/database-design/f-07-epoch-guard-fake-bootstrap-verification.md)
- [P0 目标接口设计基线](planning/interface-design-draft.md)
- [I-056 微信免确认收款授权接口设计](planning/interface-design/12-merchant-transfer-authorization-i056.md)
- [P0 详细设计与任务拆分](planning/detailed-design-draft.md)
- [P0 受控闭环正式任务](planning/tasks/p0-controlled-loop/00-index.md)

### 其他目录

- `agents/`：本地任务仓库、状态词汇和工程领域导航配置。
- `archive/`：已结束审查和历史设计快照，只用于追溯，不代表当前实现。
- `review/`：仍有参考价值的代码审查结果。
- `references/`：外部官方资料和示例代码留档，不作为本项目实现的直接规范。
- `sql/`：联调和测试辅助 SQL。

硬件侧的专用文档位于 [`hardware/docs/`](../hardware/docs/)，包括架构说明、部署说明和 UART 协议审计。它们与香橙派代码放在一起，避免主项目文档与设备运行资料再次混杂。

## 维护规则

- 新文档先确定类别再落盘，不要直接堆到 `docs/` 根目录。
- 当前行为以代码、测试和 Flyway 迁移为准；设计文档应在同一变更中同步更新。
- 历史内容移入 `archive/`，并在开头标明替代它的当前文档。
- 文档内使用仓库相对链接；移动文件时必须全仓库搜索旧路径。
- 密钥、`.env`、设备密钥、服务器凭证和真实用户数据不得写入文档。
