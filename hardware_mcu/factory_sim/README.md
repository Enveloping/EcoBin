# STM32F103C8T6 出厂外设模拟固件

这是仅用于出厂流程联调的独立固件。它运行在真实 STM32F103C8T6 上，通过真实
USART1 与香橙派通信，但在 MCU 内部确定性模拟称重、红外、烟感、屏幕按钮、投口
执行机构和清运锁。它不会初始化这些外设，也不能证明任何真实传感器或机械部件完好。

模拟固件与 `hardware_mcu/USER/` 下的生产固件没有共享构建目标，不会改变生产固件
默认行为。F3 会明确报告版本 `factory-sim-1.0.0`、版本码 `1` 和身份 `ECOSIM01`，
香橙派及验收报告可以据此永久识别模拟证据。

## 接线和串口

裸 MCU 板只需要：

| STM32F103C8T6 | 对端 |
|---|---|
| PA9 / USART1_TX | 香橙派 UART_RX |
| PA10 / USART1_RX | 香橙派 UART_TX |
| GND | 香橙派 GND |
| 3.3V | 稳定的 3.3V 电源 |

串口固定为 `115200/8N1`。不要同时从多个电源给板子供电。该固件只启用 HSI、
SysTick、GPIOA 的 PA9/PA10、USART1 和对应中断，不启用其他传感器或执行器 GPIO。

## 构建与测试

需要 Windows PowerShell 和 LLVM/Clang，默认安装位置为
`C:\Program Files\LLVM\bin`。从仓库根目录执行：

```powershell
& hardware_mcu/factory_sim/build.ps1 -Clean
```

脚本先用桌面 Clang 运行纯 C 协议状态机测试，再交叉编译 Cortex-M3 固件，最后检查
Flash 不超过 64 KiB、RAM 不超过 20 KiB。生成物位于忽略版本控制的目录：

```text
hardware_mcu/factory_sim/build/firmware/ecobin-factory-sim.elf
hardware_mcu/factory_sim/build/firmware/ecobin-factory-sim.hex
hardware_mcu/factory_sim/build/firmware/ecobin-factory-sim.bin
```

如 LLVM 安装在其他位置，传入 `-LlvmBin`。烧录完成后应先查询
`F2 01 F2`，确认 F3 身份为 `ECOSIM01`，再进入出厂验收。

## 确定性行为

- 每次收到 `F2 01 F2` 都重置称重脚本；其后 F0 第 1～4 次返回 0g，第 5～7 次
  返回 500g，第 8 次起返回 0g。F1 始终报告 `VALID=03`、红外未遮挡、烟感正常。
- 收到 `BB` 后保存模拟屏显单价；收到 `AA 01 AA` 后等待 1 秒，只发送一次
  `DD 0g→500g`。
- 收到 `EE 01 EE` 后等待 1 秒，只发送一次 `EF 500g→0g`。
- 合法 A0 URL 会保存在 RAM 中，不驱动真实屏幕，也不发送应答。
- 收到 `F2 02 F2` 后取消未完成动作、锁存升级准备并返回
  `STATUS=00/SAFE_FLAGS=1F`；复位前忽略新的 AA、BB、EE 和 A0。

验收网页中的摄像头确认、动作前安全确认、投递后的区域安全确认和清运后的门关闭
确认仍由操作员正常完成。BOOT0/NRST 与 STM32 ROM Bootloader 探测也不是本应用
固件能够模拟的能力。
