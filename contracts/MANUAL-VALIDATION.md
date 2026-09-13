# EcoBin F-10 人工验证手册

> 最新候选 [P1BT：已授权未登记发送的恢复关门撤回](../hardware/docs/review/uart2-recovery-close-withdrawal-p1bt-2026-09-13.md)：业务库先封住旧发送，永久层另存撤回事实并保留原授权历史；独立继任仅豁免准确祖先，候选循环已接自动核对，不发新动作、不启动云端、不恢复接单，业务39/永久3不变。
> 撤回专项53项与运行入口19项分别通过，最终集成/扩大回归见实施记录，完整契约25通过。已登记可能发送的未知效果、跨新启动号新动作、完整准入/云端/正常业务与main切换仍待接，P4/P5未完成。
> 未部署/烧录；OneNet2.2.0/UART rc.22/MySQL V79/权限40及发布25/39门槛不变。外部刷写/HIL不保证共用串口锁；RS485/HMI/完整固件容量保留。两项投递取舍已确认，下方历史“待确认”不再适用。

> 最新候选 [P1BH：后台确认可靠交接](../hardware/docs/review/uart2-native-confirmation-p1bh-2026-09-13.md)：原始确认、报告与回执原子绑定，schema32；322项相关测试、25项完整契约检查通过。
> 投递重启缺最终包只归档、归档后迟到结果只追加证据，两项已获用户确认，不再待裁决；实现继续按P4/P5推进。确认交接不释放占用、不修改当前袋或准入，main尚未切换。
> 未部署/烧录；发布25/32、RS485与HMI等剩余门槛保留。以下为历史记录，历史“P4待确认”不再适用于上述两项决定。

> 最新硬件候选 [P1BG：原生上报与编号落库](../hardware/docs/review/uart2-native-report-persistence-p1bg-2026-09-13.md)：实际 C 多轮/5 秒中位数/末重超时及照片快照已接可靠报告；缺测不补零，清运量为本次前重减后重。
> V78 只对齐四个业务测量编号，原质量/范围/空值约束保留；启动/供应目标同步，134 表/授权39不变。真实 MySQL 297 项、相关 Java 总计399项、Pi/隔离包50项、完整契约25项通过。
> 未部署/烧录；主程序、后端确认消费、当前袋/准入与完整异常仍待接。发布25/31、RS485、完整固件容量及 P4/HMI 待确认项保留，P3/P5/P7未整体完成。下方为历史。

> 2026-09-13 [P1BF](../hardware/docs/review/uart2-business-report-p1bf-2026-09-13.md)：清运量最新口径为清运前减清运后，后值仍为新袋皮重；跨端需成对切换并先交接旧待办，不重算历史。OneNet2.1.3/schema31为未部署候选，完整main、当前袋与云端确认消费未完成。25项合同校验通过不等于真机/MySQL验收。下方为历史。

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

> 最新运行增量[P1AF](../hardware/docs/review/uart2-clean-first-unlock-p1af-2026-09-12.md)：首次清运原命令单次通电/独立定时断电、双记录及Pi重启/丢确认精确交接通过27项专项、25项契约检查；扩大回归见记录。
> rc.17/schema25不变；不证明门位、完整清运或恢复接单，HMI/正常main/恢复仍未接，仍不可部署/烧录。下方为历史。

> 最新[P1AE](../hardware/docs/review/uart2-postclose-interruption-p1ae-2026-09-12.md)：rc.17/schema25，CLOSE后更新停止/控制上下文丢失自动记录独立原因，原输出/已有测量/选择保存后FAILED；192项相关及25项契约检查通过。
> 扩大回归1412通过/5跳过/1635子测试通过/零失败；只是本机数据交接，不证明恢复接单、完整业务或真机能力，仍不可部署/烧录。下方为历史。

> 最新[P1AD](../hardware/docs/review/uart2-interrupted-measurement-p1ad-2026-09-12.md)：rc.16/schema25，中断测量保留原身份/数量/时间，实际C/Pi精确交接后的失败结果及重启/丢确认已验证；176项定向、25项完整契约检查通过。
> 扩大回归及静态告警见记录。尚未接自动关门后异常触发/原因事件，不代表恢复接单、完整业务或真机能力；不可部署/烧录。下方为历史。

> 最新[P1AC](../hardware/docs/review/uart2-delivery-abort-p1ac-2026-09-12.md)：rc.15/schema25，CLOSE前中断保留原输出/独立原因，精确交接后FAILED；234项定向、25项完整契约验证通过，扩大回归见记录。
> 只证明本地数据交接，不证明恢复接单/真实防夹或完整业务；仍不可部署/烧录。下方为历史。

> 最新运行增量[P1AB](../hardware/docs/review/uart2-delivery-preopen-failure-p1ab-2026-09-12.md)：首次称重失败精确保存后冻结零轮次/末重未采集结果，实际Pi重启与确认丢失交接已验证，不提前清占用。
> 11项专项、140项相关回归通过；rc.14/schema25不变。扩大回归/SQLite取证见记录，未完成完整业务/main/HMI/恢复及HIL，不解除部署/烧录限制。下方为历史。

> 最新[P1AA连续投递](../hardware/docs/review/uart2-delivery-local-continue-p1aa-2026-09-12.md)：MCU原会话后续轮次/逐轮异常及本地动作来源、Pi精确保存、schema25保留数据升级已验证。
> rc.14、82项相关组合/25项完整契约验证通过；最终扩大回归1249通过/5跳过/1625子测试通过/1失败（既有Windows SQLite强杀恢复1546错误，未修复），完整异常终态/清运/HMI/账本/恢复/分类/main待接，仍不可部署/烧录。下方批次描述为历史。

> 最新运行增量[P1Z](../hardware/docs/review/uart2-delivery-finalization-p1z-2026-09-12.md)：C原窗口选择/按钮/超时、首次结果组装与Pi原文精确交接通过19项专项、92项相关回归。
> rc.13/schema24及104生成物不变；下一轮动作、HMI、完整业务/恢复/分类/main仍待接，不解除下述部署限制。

> 2026-09-12 更新：UART 唯一可变来源现为 **2.0.0-rc.20 清运中断事实交接候选，不可部署**。
> 下文 F-10、原生 1.0 和现场 checkpoint 属于历史流程，不授权将候选接入设备。
> 本地生成/三语言验证仍可执行；旧运行制品现在只做摘要核对，
> `--include-hardware-mcu` 已禁止。当前规则以 [MCU 确认单](uart/mcu-review-checklist.md)
> 和 [P1AJ运行与契约证据](../hardware/docs/review/uart2-clean-interruption-p1aj-2026-09-12.md) 为准。
> ARMCC目标需编译链接生成的`ecobin_uart_protocol.c`一次；验证工具同时运行独立头文件和共享C版本。
> 新独立生成 `hardware/uart2_protocol.py`，不是覆盖旧协议；schema27也不意味着获准对旧卡自动更新。
> 原过程数据与业务关系提交后可精确确认，但确认不授权业务继续、清占用或产生资金；完整业务接线仍待完成。
> 三类动作结果已完整校验，不复用测量保存回执；[P1T](../hardware/docs/review/uart2-actuator-journal-p1t-2026-09-12.md)又补了独立C八条内存预留/保留/释放核心。
> [P1U](../hardware/docs/review/uart2-actuator-credits-p1u-2026-09-12.md)已将记录池与全局编号额度统一接入控制入口，216项相关测试通过。
> [P1V](../hardware/docs/review/uart2-actuator-handoff-p1v-2026-09-12.md)接通实际C串口查询与Pi事务保存后的精确确认；锁通断分别交接，断电不等确认。
> 117项相关测试与25项契约验证通过。仍未接真实动作账本关系核对/单次执行/完整业务，禁止据保存成功直接接机械输出，不改变上述部署限制。
>
> 后续[P1W](../hardware/docs/review/uart2-delivery-execution-p1w-2026-09-12.md)已本地接通首次投递原命令核对、双100ms断开/中断自动关门、Pi真实发送与双库交接；11项执行专项通过。
> rc.12/schema23不变；关门后测量/完整业务、清运、账本效果核销/恢复与正常main仍未接通，仍不可部署或烧录。
>
> 再后续[P1X](../hardware/docs/review/uart2-delivery-postclose-p1x-2026-09-12.md)已接关门后按原配置等待/独立5秒采样/原轮次精确保存，10项新专项通过；rc.12/schema23不变。
> 当时选择记录、完整结果、清运/HMI、账本效果对账/恢复和正常main未接通，不得将等待选择阶段当作业务已完成。
>
> 最新[P1Y](../hardware/docs/review/uart2-delivery-selection-custody-p1y-2026-09-12.md)已接通选择与原轮次已保存重量关联、C保留查询与Pi同事务保存后的精确确认；rc.13/schema24。
> 52项定向、25项契约验证通过，43帧/1242流轨迹/9摘要三语言一致。选择产生/HMI倒计时、继续/结束执行、完整结果、清运、账本/恢复/main仍待接，保持上述全部部署限制。

本手册用于复现 F-10 的机器契约验证，并为选择 `uart-v1` 原生 MCU 实现时提供附加
符合性步骤。验证分为四层：

1. 本地生成物和 Python 规则；
2. Java 21 / 通用 C11 跨语言黄金样本；
3. OneNet 测试产品导入与 OneJSON 收发；
4. 可选的原生 UART 1.0 MCU 逐字段确认。

F-10 已于 2026-07-27 收口。现有固定帧单片机不需要执行第 7～8 节；其线路和真实
物理行为按 F-11/H-03 的固定帧适配验收。若以后选择 `uart-v1` 原生 MCU 实现，再使用
[`uart/mcu-review-checklist.md`](uart/mcu-review-checklist.md) 验证工具链、门、锁、
断电和恢复，不能用 F-10 软件证据冒充部署验收。

## 1. 验证前准备

- 保留现有 OneNet 生产物模型的导出备份，不直接在生产产品上试导入。
- 新建或选择一个 MQTT + OneJSON 的非生产测试产品。
- 使用 Java 21；香橙派代码目标仍是 Python 3.11。
- 准备通用 C11 编译器；若部署 `uart-v1` 原生 MCU，再准备其实际固件编译器。开发机
  没有 C 编译器时，自动校验中的 `NOTE` 不否定已经归档的 F-10 证据。
- 不把真实 OneNet Key、设备 Key、COS 临时密钥或服务器凭证写入样例或验证记录。

## 2. 本地软件验证

在仓库根目录依次执行：

```powershell
python --version
python contracts/tools/generate_contracts.py --check
python contracts/tools/validate_contracts.py
python -m unittest discover -s contracts/tests -v
```

上述默认流程不会覆盖 `hardware_mcu/` 或 `hardware/uart_protocol.py`。
候选阶段禁止 `--include-hardware-mcu`，不要执行历史导出命令。

香橙派或其他明确安装了目标解释器的环境使用 `python3.11` 替换上述 `python`。如果
`python --version` 不是 3.11，只能证明当前解释器下的行为；单元测试中的 3.11 grammar
检查不能替代至少一次真实 Python 3.11 执行。

预期：

- 生成检查没有 drift；
- 18 种 OneNet 命令、22 种事件/回执全部通过；
- OneNet 导入候选共 40 个功能点；
- OneNet 导入候选使用 LF、严格小于 256 KiB；枚举显示说明为 1～20 个允许字符；
- 每个服务输入/输出分别不超过 20 项，每个事件输出不超过 50 项；功能标识不超过 50
  字符，显示名不超过 30 字符；
- 40 份 OneJSON 线级样例与导入候选一致；
- Python 与 Java 的 JCS/稳定身份摘要一致；
- UART 当前 55 个消息、31 个帧向量、384 个流式解析轨迹和 4 个摘要向量通过；
- 单元测试全部为 `OK`；
- 若机器没有 C 编译器，只允许出现“C compiler unavailable/skip”的说明。

任何失败都先修改权威 Schema、Registry 或生成器，再重新生成。不要直接编辑
`contracts/**/generated/` 或 `contracts/examples/`。

## 3. Java 21 单独验证

自动校验会在可找到 `javac/java` 时执行本节。需要单独复现时：

```powershell
New-Item -ItemType Directory -Force contracts/.tmp-javac-f10 | Out-Null
javac --release 21 -d contracts/.tmp-javac-f10 `
  contracts/uart/generated/java/EcobinUartProtocol.java `
  contracts/uart/generated/java/EcobinUartGoldenTest.java `
  contracts/onenet/generated/java/EcobinCanonicalJson.java `
  contracts/onenet/generated/java/EcobinCanonicalJsonGoldenTest.java
java -cp contracts/.tmp-javac-f10 EcobinUartGoldenTest
java -cp contracts/.tmp-javac-f10 EcobinCanonicalJsonGoldenTest
```

预期最后两条分别输出：

```text
Java UART golden vectors: 11 frames, 10 stream traces, 3 digest profiles passed
Java OneNet canonical vectors: 4 payloads, 7 stable identities passed
```

JCS 负例还必须拒绝浮点、超出 `±9007199254740991` 的整数和未配对 surrogate。
JSON 解析入口必须拒绝重复对象键，不能在进入摘要算法前静默采用“最后一个值”。

## 4. OneNet 控制台导入

导入文件：

[`onenet/generated/onenet-thing-model.candidate.json`](onenet/generated/onenet-thing-model.candidate.json)

在非生产产品中导入后核对：

| 项目 | 预期 |
|---|---:|
| 属性 | 0 |
| 同步服务 | 18 |
| 事件 | 22 |
| 总功能点 | 40（低于 OneNet 的 100 个功能点上限） |
| 导入文件 | `192207` bytes、小于 256 KiB、LF 换行 |
| 枚举显示说明 | 1～20 个中英文、数字、下划线或连字符 |
| 单服务输入/输出 | 各不超过 20 |
| 单事件输出 | 不超过 50 |

2026-09-10 当前候选的 SHA-256 为
`5f5eb993527a899eed0193dd34d7c57842ff002f676126062d5c082ca369bea1`，距离
`262144` bytes 上限还剩 `69937` bytes。导入时必须选择仓库内的生成文件，不要用编辑器
重排 JSON、转换换行或另存副本。

每个服务都应为同步调用，并具有相同的 6 个即时回复字段：

```text
schemaVersion
commandUid
receiptState
errorCodePresent
errorCode
edgeBootId
```

同步回复只允许证明香橙派已经校验并把命令可靠保存到 SQLite。OneNet 同步调用窗口为
5 秒，不能在回复前等待开门、称重、拍照、MCU 完整作业或后端业务完成。

OneNet 物模型只负责平台能表达的类型和范围。以下规则仍由香橙派和后端使用权威 JSON
Schema/语义校验器执行：

- 字符串枚举在 OneNet 线上编码为整数，接收后先按生成映射还原为 JSON 符号；
- 可空字段使用 `<field>Present + typed placeholder`，先还原为 JSON `null`；
- 还原后才计算 `payloadSha256` 并执行跨字段规则；
- `struct` 只有一层，`struct` 成员不包含数组，协议中没有浮点重量或单价。
- 数组描述使用控制台导出格式 `specs.size + specs.items`，长度值使用字符串；
- 超过服务顶层参数上限时，生成器把原始标量无损分组到
  `scalarFields`/`scalarFieldsN` 一层结构（每组不超过 20 个成员）；对应 `jsonPath`
  和还原方式以生成的 wire mapping 为准。

本候选使用的 OneNet 平台边界可对照以下官方资料：

- [物模型功能点与数据类型](https://onenet.hk.chinamobile.com/doc/v5/fuse/detail/199)：
  功能点不超过 100，`struct` 只支持一层且成员不支持数组；
- [中国移动物联网物模型标准白皮书](https://upfiles.heclouds.com/portal5-admin/portal5-admin/2022/03/14/4ed1223472bf7e25d16f021f5b839b5b.pdf)：
  服务输入/输出、事件输出、标识符和显示名数量/长度限制；
- [物模型查询返回结构](https://iot.10086.cn/doc/iot_platform/book/api/common/queryThingModel.html)：
  `functionMode`、服务/事件参数和类型描述结构；
- [OneJSON 设备服务](https://iot.10086.cn/doc/iot_platform/book/device-connect%26manager/thing-model/protocol/OneJSON/service.html)：
  同步调用超时 5 秒及 invoke/invoke_reply 格式；
- [OneJSON 设备事件](https://onenet.hk.chinamobile.com/doc/v5/fuse/detail/202)：
  event/post 与 event/post/reply 格式；
- [物模型服务调用 API](https://onenet.hk.chinamobile.com/doc/v5/fuse/detail/312)：
  `thingmodel/call-service` 的请求和响应边界。

平台文档和控制台可能演进；如果当前测试产品的真实导入或调用行为与资料不同，以原始控制台/API
结果作为阻塞证据，回到机器源调整，不在控制台中维护第二套手工定义。

完整投影规则见：

[`onenet/generated/onenet-wire-mapping.json`](onenet/generated/onenet-wire-mapping.json)

如果控制台拒绝导入：

1. 记录控制台原始错误、功能点和字段名；
2. 不手改导入 JSON；
3. 回到 Schema/生成器修正；
4. 重新执行第 2 节，并重新导入一个干净的测试产品版本。

## 5. OneNet 同步服务验证

首选先验证：

[`examples/onenet-wire/start-delivery-session.service-wire.json`](examples/onenet-wire/start-delivery-session.service-wire.json)

步骤：

1. 将 `product_id` 和 `device_name` 占位符替换为测试产品和测试设备；
2. 使用 OneNet 控制台应用模拟器或官方 `thingmodel/call-service` API 调用
   `startDeliverySession`；
3. 设备订阅样例中的 `deviceInvokeTopicTemplate`；
4. 设备完成 Schema、部署、摘要、截止时间校验和 SQLite 提交；
5. 在 5 秒内复制请求 `id`，按 `deviceAcceptedReplyTemplate` 发布同步回复；
6. 核对应用侧返回 6 个即时回执字段。

至少再验证：

- 同一个 `commandUid + stable digest` 重复下发返回 `DUPLICATE_ACCEPTED`，不重复物理动作；
- 同一个 `commandUid` 改目标、期限或稳定载荷时返回 `REJECTED`；
- 只刷新 `cosGrant` 时稳定命令摘要不变；
- `cosGrant.bucket/region/baseUrl` 必须与可信部署配置逐项相等，照片 URL 的 origin
  也必须相等；设备或载荷不能自行指定另一个 COS 环境；
- 过期命令、新部署不匹配、SQLite 无法提交时均不得返回 `ACCEPTED`；
- OneNet 调用成功或回执 `ACCEPTED` 均不得被记录为门已打开或订单已完成。

`examples/onenet-wire/` 中为全部 18 个服务提供了同格式样例。样例中的 COS 凭证是假的，
只用于类型/协议验证，不能用于真实上传。

## 6. OneNet 事件验证

首选先验证：

[`examples/onenet-wire/delivery-complete.event-wire.json`](examples/onenet-wire/delivery-complete.event-wire.json)

步骤：

1. 设备向样例中的 `$sys/{pid}/{device-name}/thing/event/post` 发布
   `oneJsonPayload`；
2. 订阅 `/thing/event/post/reply`，确认相同消息 `id` 收到 `code=200`；
3. 在 OneNet 北向 MQ 中确认事件标识符、认证 `productId/deviceName` 和参数完整；
4. 后端先还原 enum/null，再校验 JSON Schema、`payloadSha256`、部署和目标；
5. 后端权威事务完成后才下发 `CONFIRM_EDGE_EVENT`；
6. 设备持久化确认后上报 `BUSINESS_CONFIRMATION_RECEIPT`。

重点检查：

- 一个投递 `sessionUid` 无论中间继续多少次，只上报一个 `DELIVERY_COMPLETE`；
- `deliveryNetWeightGrams = 最终关门稳定重量 - 首次开门前稳定重量`；
- `negativeWeightAnomaly` 只是一项最终布尔标志，不携带中间减少值；
- 清运完成必须有电磁阀断电、健康正常及清运员人工关门/完成确认；无门磁时物理门位
  保持 `UNKNOWN`，不能由锁状态推定关闭；
- 照片不完整不阻止订单/清运，`PHOTO_STATUS_REPORTED` 只补报
  `AVAILABLE/PERMANENTLY_MISSING`；
- `AVAILABLE` URL 必须精确命中部署、作业、槽位和 `photoUid`；
- OneNet PUBACK、事件上报 `code=200` 和 Pulsar ACK 都不是后端业务确认。

`examples/onenet-wire/` 中为全部 22 个事件/回执提供了可发布样例。

## 7. 候选 C 工具链验证（不可导入当前固件）

将以下两份文件放入 MCU 的实际 C11 工具链：

- [`uart/generated/c/ecobin_uart_protocol.h`](uart/generated/c/ecobin_uart_protocol.h)
- [`uart/generated/c/ecobin_uart_golden_test.c`](uart/generated/c/ecobin_uart_golden_test.c)

若工具链提供类 GCC 命令，可参考：

```text
<cc> -std=c11 -Wall -Wextra -Werror \
  -Icontracts/uart/generated/c \
  contracts/uart/generated/c/ecobin_uart_golden_test.c \
  -o ecobin_uart_golden_test
```

运行后预期：

```text
C UART golden vectors: 24 frames, 136 stream traces, 3 digests passed
```

该程序验证帧/CRC/摘要原语，并在新增 9 条引导/会话消息交给回调前验证内容；不代表剩余
39 条消息具备完整生产内容执行器。候选 `>512 bytes` 增量排空、坏帧与尾部半帧超时已通过。
跨业务消息集合/状态验证等仍见
[`DEFERRED-HARDENING.md`](DEFERRED-HARDENING.md)，候选禁止用于设备。

同时核对生成头文件中的：

```text
115200 baud / 8N1 / no flow control
magic EC 42
big-endian
maximum frame 256 bytes
maximum payload 242 bytes
receive buffer 512 bytes
frame assembly deadline 100 ms
ACK timeout 500 ms
generic maximum sends 3; bootstrap/commands OVERRIDE to 1 (not yet wired)
CRC-16/CCITT-FALSE: "123456789" -> 29B1
```

## 8. 可选：原生 UART 1.0 MCU 人工 checkpoint

逐项完成：

[`uart/mcu-review-checklist.md`](uart/mcu-review-checklist.md)

必须记录：

- Registry 版本和 SHA-256；
- MCU 固件/工具链版本；
- 39 个消息号、字段偏移、方向和 ACK 规则的确认结论；
- capability bit 0～14 的真实支持情况；
- 非零 UUID 默认规则、明确的零 UUID sentinel、`PortFaultBitmap` bit 0～4 与保留位规则；
- 快照 applied/staging 全有或全无、partCount/bitmap 恢复约束；
- 危险命令去重、关键事件队列、配置和作业状态所用非易失介质、容量与擦写边界；
- C 黄金样本输出；
- 不能实现或需要调整的字段及原因。

若原生 UART 1.0 MCU 无法跨看门狗/断电保存不可逆动作的去重和关键事件，该部署不能
宣称符合相应 capability；固定帧模式同样不能让香橙派用猜测或超时成功替代缺失事实。

## 9. 验证记录

完成后建议在 MCU 确认单尾部追加：

```text
验证日期：
仓库 commit：
OneNet 测试产品：
物模型导入：通过 / 失败（原始错误）
同步服务：通过 / 失败
事件上报与北向 MQ：通过 / 失败
Python：
Java 21：
MCU C 工具链：
Registry SHA-256：
MCU 负责人结论：
遗留项：
```
