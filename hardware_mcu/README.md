# EcoBin STM32F103C8T6 固件工程

本目录包含可重建 MCU 固件所需的 Keil 工程、业务源码、CMSIS 和 STM32F10x
标准外设库。工程目标芯片为 `STM32F103C8T6`，入口工程为
`USER/STM32-DEMO.uvprojx`，当前使用 ARM Compiler 5 配置。

## 版本库边界

纳入版本管理的是构建输入：

- `USER/` 下的 MCU 业务源码、头文件和 Keil 工程；
- `CMSIS/` 与 `FWlib/`；
- `USER/uar/` 下由契约生成器产生的 C 协议产物；
- `tests/` 下可由桌面 Clang 运行的纯 C 状态转换测试。

不纳入版本管理的是 `Output/`、`Listing/`、历史备份、J-Link/Keil
个人配置，以及每次发布单独生成的 `USER/firmware_identity.h`。这样不会把旧固件、
开发机状态或某一次发布身份误当成源码提交。

MCU 与香橙派之间的完整通信协议只维护在
`hardware/docs/单片机-香橙派适配通信协议详细内容.md`；本目录不再保留删节版或副本。

## 构建前生成固件身份

从仓库根目录执行以下命令。版本码必须单调递增；生成的 JSON 和头文件属于同一次
发布，不能拆开复用。

```powershell
uv run --python 3.11 python hardware/mcu_firmware_package.py identity `
  --version 1.0.0 `
  --version-code 10000 `
  --header hardware_mcu/USER/firmware_identity.h `
  --metadata release-output/mcu-1.0.0.identity.json
```

`USER/firmware_identity.example.h` 只说明头文件形状，不能作为正式发布身份。

## 使用 Keil 构建

1. 安装 Keil MDK、ARM Compiler 5.06 update 6 或兼容版本，以及
   `Keil.STM32F1xx_DFP` 设备包。
2. 先按上一节生成 `USER/firmware_identity.h`。
3. 打开 `USER/STM32-DEMO.uvprojx`，选择 `USART1-DEMO` 目标并执行 Build。
4. 工程链接完成后会调用 `fromelf`，生成
   `Output/STM32-DEMO.bin`。该文件是后续签名打包的输入，不应提交到 Git。

## 本机逻辑测试

F2 模式 02 的“停止全部输出并锁存升级执行模式”转换不依赖 STM32 寄存器，可用
Clang 在桌面直接回归：

```powershell
cd hardware
uv run --python 3.11 --with pytest pytest -q tests/test_mcu_update_execution_c.py
```

该测试只验证纯 C 转换；发布前仍需用真实 STM32、香橙派 UART 和 BOOT0/NRST
接线执行硬件在环验收。

如修改 `contracts/uart/` 中的契约，还应从仓库根目录重新生成并校验 MCU C 产物：

```powershell
uv run --python 3.11 python contracts/tools/generate_contracts.py `
  --include-hardware-mcu --check
```
