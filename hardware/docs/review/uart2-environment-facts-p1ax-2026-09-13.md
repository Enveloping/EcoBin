# P1AX：接单前环境事实补齐（尚未接通完整准入）

日期：2026-09-13。状态：原生候选本地实现/验证，未部署、未烧录。

上一批后端满溢支持见 [P1AW](uart2-fullness-state-median-p1aw-2026-09-13.md)。

## 1. 为什么本批先补事实

检查当前实际 WorkManager.start_delivery_command 及 _safety_rejection：
旧固定帧入口检查 F1 自检、烟感、当前袋满溢和清运重启锁；
它不是一个可以简单将 STABLE 改成 TIMEOUT_MEDIAN 的原生称重判断。
新 McuCommandDispatcher/永久动作账本仍要求业务所有者提供真实前置检查，
并没有因为一个独立策略测试通过就完成主程序接线。

新 DEVICE_FACTS_REPLY 此前有门目标/输出、防夹、配置、当前秤读数、历史测量和业务摘要，
却缺烟感与满溢传感器来源。不能在这种情况下把测试中的“健康”常量接到实际接单门槛。
本批补齐这部分真实观察表达，并接入现有烟感状态机；**不是准入完成或自动恢复接单完成**。

## 2. 现在能提供什么

同一只读状态快照新增七个字段：

- smokeObservationState / smokeObservedUptimeMs：未观察、正常、报警、不可用及其观察时间。
- fullnessObservationKind / fullnessReadStatus / fullnessCapturedUptimeMs：
  未知/超声波/数字红外、未观察/有效/不可用和实际采集时间。
- fullnessInfraredBlocked / fullnessDistanceMm：数字红外真实遮挡状态或超声波毫米距离，
  不能在同一次事实里混用。毫米数据不是旧驱动的厘米值，也不是满溢百分比。

缺来源默认 NOT_OBSERVED，不补成 NORMAL/CLEAR；读取失败用 UNAVAILABLE，
不复用旧距离或遮挡值冒充成功。零距离、零时刻可以是真实观察，是否有值依据状态而不是零值。
超声波无回波等旧驱动失败哨兵必须由实际采集适配显式映射为不可用，不能作为有效毫米值。
业务是否满溢仍由配置和真实证据计算，不在事实采集接口中偷偷决定。

McuDeviceFacts 的前台发布接口拒绝未来时间、较旧数据、同一时间的矛盾内容、
不合法来源/字段组合；完全相同的同时间重发可重复接受，但不刷新时间。
查询仅复制事实，不采样、不改事实、不清结果/占用、不操作 GPIO，也不是机械许可。
原启动或投口不匹配时，环境事实和其他正文一起清零，外层状态仍明确不匹配。

## 3. 烟感已有真实生产者，满溢采集尚待接线

新 McuEnvironmentMonitor_PollSmoke 调用真实 SmokeMonitor_UpdateSample，
只有确实完成一次 ADC 尝试时才把原防抖状态与完成时刻发布到设备事实。
明确转换旧烟感枚举，不能将旧 CC 的 0=NORMAL 当成新协议的 0=NOT_OBSERVED。
它没有伪造一次新的物理观察；重复前台轮询、50 毫秒间隔未到或预热等待不发布新数据。

原 SmokeMonitor_Update 仍保留为兼容包装，原有预热、阈值、迟滞、防抖、
读取失败判定和时钟回绕行为不变。新状态仍是烟感模块的防抖结果，
不是每个 ADC 原始电压或独立实时电气健康证明。

本地测试使用真实烟感/计时/设备事实 C 模块，只替换 ADC 和 GPIO 硬件边界：
预热不假报正常，报警按三次规则进入，正常恢复按二十次规则进入，
ADC 失败按原十次规则进入不可用。设备事实经真实生成的编码、
香橙派 McuDeviceFactsQuery 与 SQLite 查询编号分配交接，超时观察为未知；
后来的新查询仍能看见旧采集时间，不会把旧 NORMAL 刷新为新观测。

满溢目前完成事实接口和合法组合验证，**没有宣称实际传感器采集已接入**。
当前 main.c 仍有旧阻塞式 HCSR04 厘米测距，不能在只读查询里调用它并驱动 TRIG，
也不能据名称猜测现场一定是数字红外。实际来源和原生前台采集须继续接好。
烟感生产者同样尚未挂到新的正式主循环；新设备事实仍不是完整上线安全检查。

## 4. 契约、容量与验证

UART 候选推进为 **2.0.0-rc.21 / ENVIRONMENT_FACTS_NOT_RUNNABLE**。
只改 DEVICE_FACTS_REPLY 形状：正文从 204 增至 226 字节，完整帧为 240 字节。
保留 67 条消息、50 个黄金帧、16 个摘要配置；没有加新机械命令。
旧 rc.20 的正确 CRC 状态包也必须被明确拒绝，不能补默认健康字段兼容接单。
新 Pi 与 MCU 候选未来必须成对切换；冻结的旧运行 UART 三个制品未改。

Registry SHA-256：
`77f5a310a8e1bff7f881168e07ffd2ee7cd0f85d4aca71c6889ebf325a9edf16`。

按 TDD 从实际 C 快照缺少环境字段的失败测试开始，再补生成源、
三语言校验与 C 发布接口；第二条用例确认发布 API 原先确实不存在。
新旧编解码、字段组合和实际烟感来源分别验证，不以单独字典判断冒充业务接线。

结果：

- 新增 26 项设备事实/真实烟感链路用例；设备事实文件共 33 项，烟感链路 2 项。
- 70 项定向测试通过，包括实际 C、Pi 查询、旧控制模块、查询范围与核心目标编译。
- 60 个相关测试文件扩大回归：**1199 passed**，0 失败/错误/跳过，耗时 299.13 秒。
- 契约测试：**441 passed, 1694 subtests passed**。
- 完整机器契约校验：**25 passed, 0 notes**；104 份生成物一致，
  1734 条流轨迹经 Python、Java 21 和两种 C 实现验证。
- Cortex-M3 核心链接探针 ROM **54,472** 字节、RW/ZI **6,240** 字节。
  包含新事实接口和烟感生产者，使用仅链接用 ADC 边界。
  不是实际 STM32 ADC 驱动/启动代码、完整 IRQ 栈、完整 Keil 固件或现场安全证明。
  不可烧录该探针。

扩大回归选择实际原生模块，不包含完整硬件工程全部测试：

```powershell
$files = rg --files hardware/tests | Where-Object {$_ -match '(test_native_|test_mcu_(?!firmware|update|safe_gpio)|test_uart2_transport|test_uart_protocol\.py|test_onenet_wire\.py|test_runtime_source_schema)'}
& 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe' -m pytest @files -q
```

复用现有 Python 3.11、Clang、ARMCC、Java 缓存，不下载依赖。
本批没有创建或操作 Docker/MySQL；V77、授权清单39、OneNet2.1.2、
业务 SQLite schema30/永久层 schema3 均不变。

## 5. 后续与未解除边界

继续实际满溢传感器生产者、当前健康与历史测量的年龄核对，再将这些事实接到真实原生
业务前置检查和主循环；不能让一个孤立策略函数或模拟健康回调替代接单路径。
还需原生结果上云、业务分类、主程序、P6 展示/HMI，P3/P5 未整体完成，P7 不可发布。

P4 两项资金裁决、真实 RS485 迟到回复归属、HMI 返回交互及发布清单25/30待办保留。
既有 Windows SQLite 强杀恢复1546本批未专项复测，不能宣称已修复。
未 SSH、未操作设备 UART/GPIO、未部署/烧录/导入物模型或修改 HMI；无需现场操作。
