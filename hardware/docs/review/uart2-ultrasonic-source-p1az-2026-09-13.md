# P1AZ：非阻塞超声波采集与原生环境事实

日期：2026-09-13。状态：本批候选本地实现/验证完成，未部署、未烧录。
上一批见 [P1AY 原始秤读取](uart2-scale-observation-p1ay-2026-09-13.md)。

## 1. 为什么继续这一批

旧 main.c 的 HCSR04_GetDistance 在前台循环等待回波，以循环次数判断超时，
使用 TIM4 获取脉宽，再先截断成整厘米。P1AX 有原生环境事实字段，却没有超声波生产者；
把旧阻塞调用放进状态查询会使“查询”变成实际传感器动作，并继续占用前台处理时间。

本批新增独立的非阻塞采集所有者、真实 STM32 寄存器适配和原生事实发布路径。
旧 main.c / HCSR04_Init/GetDistance 与现用 Keil 工程没有切换到新入口。
**实际驱动代码已编译/链接和本地执行验证，不等于真机已经使用它。**

## 2. 实际调用链与数据含义

- UltrasonicStm32_Init 只在未来原生模式的 MCU 启动时调用一次，独占 PA11/TRIG、
  PA12/ECHO、EXTI12 和 TIM4。沿用当前板子的引脚和 ECHO 上拉方式，不触碰 PB5/6/7/8。
- McuEnvironmentMonitor_StartUltrasonic 从实际已完整提交的配置读取原投口类型和回波期限，
  另核对已应用记录的版本、全配置摘要、MCU 子集摘要以及配置正在暂存的状态。
  未应用、摘要不一致、投口关闭、非本板投口1或数字红外配置均不发出超声波脉冲。
  它不把“完整收到配置”自己写成“全机已应用”。
- UltrasonicReader_Begin 只启动一次触发脉冲并立即返回；定时器中断结束脉冲、管理期限，
  ECHO 引脚中断记录上升/下降观察。没有循环等待、自动重试、连续采样或 GPIO 查询副作用。
- 完成后保留原请求号、脉宽、观察时间与可用状态。前台
  McuEnvironmentMonitor_PollUltrasonic 发布成功后才精确退役该记录。
  发布失败或交给错误投口不会丢掉结果，也不能开始新采样覆盖它。
- 有效距离沿用旧驱动的 pulse_us / 58 厘米换算基础，但直接算 pulse_us * 10 / 58 毫米，
  不先截断为整厘米。5800 微秒对应 1000 毫米；这不是精度校准，也不是满溢百分比。
- DEVICE_FACTS_REPLY 的来源为 ULTRASONIC；距离与采集时间来自实际完成记录。
  晚 100 毫秒发布不会把旧回波时刻更新为发布时刻，反复查询不触发/重测/刷新。

“本批读取成功”只表示取得了本次回波观察，不等于设备允许接单。
这里没有依据距离直接修改当前袋 FULL/NOT_FULL、解除业务占用或产生资金。
fullnessMode、距离阈值、采样数量、最少有效数量和袋身份还要由后续满溢业务所有者使用；
新增配置投影只用于原始采集，未冒充整组满溢规则实现。
原契约的 NO_ECHO_CLEAR_FALLBACK / INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK
属于整组业务取值依据；原始一次读取不可用与该回退规则是不同层次。
本批既不取消原回退，也不将缺失的回波冒充一次测得 CLEAR，更不恢复 V25 已退役的后端主动检测任务。

## 3. 时间、异常与硬件边界

原配置允许的回波期限 100..100000 微秒全部支持，不暗改成固定 40 毫秒或限制为 16 位。
TIM4 以当前板子的 72 MHz 来源分频到 1 MHz，16 位计数器溢出累计为 32 位微秒时间。
100000 微秒期限的低 16 位会提前匹配一次；驱动核对完整期限后才执行到期处理。
ECHO 中断先于溢出服务时，会计入尚未服务的溢出，但不提前消费、重复累计它。

驱动选择 15 微秒触发脉冲、至少 70 毫秒的请求起点间隔。
这些是采集驱动时序，不改云端的满溢阈值/数量；70 毫秒符合厂家建议大于 60 毫秒的周期，
15 微秒满足至少 10 微秒触发要求。[ELECFREAKS 官方 HC-SR04 说明](https://www.elecfreaks.com/blog/post/hc-sr04-demo-for-arduino.html)
定时器/外部中断依据现有 ST CMSIS、标准外设库和寄存器定义，参考
[ST RM0008](https://www.st.com/resource/en/reference_manual/cd00171190.pdf)。

- 起始 ECHO 已经为高、无上升沿、缺下降沿、零脉宽、期限后的边沿、边沿顺序矛盾，
  都得到 UNAVAILABLE，距离字段为零占位，而不是复用旧距离或假报“未满”。
- 已完成后迟到边沿不能覆盖原记录；无请求时的 GPIO 活动不生成测量。
- 原结果未退役、请求仍在进行或触发间隔未到时，拒绝新请求，不突发补采。
- 采集时间是中断观察时的 RuntimeClock 时间；旧板时钟由 TIM3 的 10 毫秒节拍推进。
  这不是硬件输入捕获锁存时刻，也没有宣称微秒级时间戳或现场 3 毫米精度。
- 若期限中断服务延后，失败时间记录实际发现不可用的中断观察时刻；迟到回波仍先核对
  原期限，不能因超时中断尚未运行就把它接受成正常数据。前台晚查询不会再次刷新该时间。
- 中断延迟、漏沿、临界区最长时间、实际 TIM4 时钟和电平兼容仍需 HIL 实测。
  扩展计数的前提是中断服务不漏掉整个 65536 微秒周期；没有凭软件假定任意长关中断可恢复。
- 必须在未来模式切换时移除旧 HCSR04/TIM4 的同资源调用；禁止两个驱动并行运行。
  EXTI15_10 目前仅服务 EXTI12；以后若启用其他共享线路，必须扩展统一分发。

## 4. 验证与真实目标容量

按 TDD 分步补齐：首先验证生产者缺失；再验证配置驱动启动入口缺失；
边沿顺序异常测试实际失败于仍然等待，修正后明确冻结不可用记录。
不以仅传入一份“距离正常”字典代替真实采集链路。

新增 32 项用例，含两层真实代码：

1. 实际 C 状态机、配置接收/提交、设备事实和原生编码；仅替换 GPIO、单次定时器和时钟边界。
2. 实际 ultrasonic_stm32.c 寄存器适配执行；测试模拟寄存器、清中断副作用和计数器，
   覆盖 PA11/12 配置、其他引脚/EXTI13 状态保留、16 位回绕、100 毫秒期限提前匹配、
   未服务溢出的 ECHO 记录，以及期限中断延后时拒绝迟到回波。

- 六个定向文件：109 passed，0 失败/错误/跳过，59.40 秒。
- 契约测试：441 passed、1694 subtests passed，73.39 秒。
- 完整机器契约校验：25 passed、0 notes；104 份生成物一致；67 条消息、50 黄金帧、
  1734 流轨迹、16 摘要经 Python、Java 21、两种 C 实现通过。
- 61 个相关文件扩大回归：1258 passed，0 失败/错误/跳过，312.25 秒。
- Cortex-M3 核心链接探针：ROM **59852** 字节、RW/ZI **6376** 字节。
  与 P1AY 相比增加 ROM 5040、RAM 112 字节；64 KiB ROM 仅余 **5684** 字节。
  本次链接保留实际 STM32 适配/IRQ、ST 定时器/EXTI 库和 CMSIS，并非将驱动替换为成功常量。
  指定 STM32F10X_MD 与 USE_STDPERIPH_DRIVER；首次库编译因未启用标准库宏出现的
  assert_param 告警已通过正确编译配置解决，没有屏蔽告警。
  该探针仍不含完整 main/HMI/UART 收发调度、启动和中断栈验证，不能据此宣称完整固件可发布，
  也不能烧录探针。P7 要继续核对完整目标容量和必要的体积优化。

扩大回归复跑命令（不是整个 hardware 工程全量测试）：

```powershell
$files = rg --files hardware/tests | Where-Object {$_ -match '(test_native_|test_mcu_(?!firmware|update|safe_gpio)|test_uart2_transport|test_uart_protocol\.py|test_onenet_wire\.py|test_runtime_source_schema)'}
& 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe' -m pytest @files -q
```

本机源文件字节 SHA-256：

- ultrasonic_reader.c：`1d57855a66edb8edc5f7bcfb08b056287a00b436db122c10a3c81b6ef5a35598`
- ultrasonic_stm32.c：`3b8ca5a1ab5fc8470aac8eadef0e15949bf0b103b829299ff253c30668354f89`
- mcu_environment_ultrasonic.c：`bf0a54ab6cf3c545670c41da46e995819ceeaa3c0df3ccebf51671734170df63`
- mcu_config_collection.c：`2c87a8dea0a51de2e195bbd5ecc6ca13805c62a347df202e9b9389213ceb2c72`

## 5. 当前仍不能发布的原因

UART rc.21 / ENVIRONMENT_FACTS_NOT_RUNNABLE、OneNet2.1.2、V77/授权39、SQLite schema30/3 不变。
未新增命令或放宽任何传感器/配置契约，也未替换旧三份 UART 运行制品。
还需按原袋/配置执行多次满溢采集与计算、接入正常主循环及完整接单检查、原生结果上云、
业务分类和 P6 HMI/展示。P3/P5 未整体完成，P7 不可发布。
P4 两项资金裁决、真实 RS485 迟到帧归属、HMI 返回交互、发布清单25/30及
Windows SQLite 强杀恢复1546问题保留；本批不声称修复这些事项。

复用 Python3.11、Clang、ARMCC5、Java21 缓存，无依赖下载。
仅运行本地临时测试/链接，不 SSH、不操作现场 UART/GPIO、不刷卡/烧录/部署/修改 HMI，
不操作云端或真实数据库。当前无需用户参与。
