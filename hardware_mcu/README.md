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

当前UART v2协议的唯一机器来源是`contracts/uart/uart-registry.yaml`，生成的C产物位于
`USER/uar/`。`hardware/docs/单片机-香橙派适配通信协议详细内容.md`只保留历史fixed-frame
线路依据，不能作为rc.27字段定义。

## 出厂外设模拟固件

`factory_sim/`是完全独立的历史fixed-frame测试固件和Clang构建目标，不是当前rc.27 P7
量产验收模拟器。它通过
真实 USART1 与香橙派通信，但在 MCU 内部模拟缺失的称重、红外、烟感、屏幕按钮和
机械动作，供设备电源暂时无法带动外设时演练正常出厂流程。其 F3 身份固定为
`ECOSIM01`，不能冒充本目录下的生产固件或真实硬件在环证据。

构建和本机测试从仓库根目录执行：

```powershell
& hardware_mcu/factory_sim/build.ps1 -Clean
```

该目标只需要 LLVM/Clang，不需要 Keil。接线、模拟时序、烧录产物位置和安全边界见
`factory_sim/README.md`；全部线路字节定义仍只以完整通信协议文档为准。

## 构建前生成固件身份

从仓库根目录执行以下命令。版本码必须单调递增；生成的 JSON 和头文件属于同一次
发布，不能拆开复用。

```powershell
uv run --project hardware --python 3.11 python hardware/mcu_firmware_package.py identity `
  --version 1.0.0 `
  --version-code 10000 `
  --application-protocol-family ECOBIN_UART `
  --header hardware_mcu/USER/firmware_identity.h `
  --metadata release-output/mcu-1.0.0.identity.json
```

`USER/firmware_identity.example.h` 只说明头文件形状，不能作为正式发布身份。
当前 `.efw` schema 1 与远程升级器只支持旧 `FIXED_FRAME` 固件；上述
`ECOBIN_UART` 身份可以用于本地构建和人工烧录，但打包命令会明确拒绝，不能把新版
固件冒充为固定帧修订号 2 后远程下发。

## 使用 Keil 构建

1. 安装 Keil MDK、ARM Compiler 5.06 update 6 或兼容版本，以及
   `Keil.STM32F1xx_DFP` 设备包。
2. 先按上一节生成 `USER/firmware_identity.h`。
3. 打开 `USER/STM32-DEMO.uvprojx`，选择 `USART1-DEMO` 目标并执行 Build。
4. 工程链接完成后会调用 `fromelf`，生成
   `Output/STM32-DEMO.bin`。该文件是后续签名打包的输入，不应提交到 Git。

## 本机逻辑测试

F2 模式 02 的“停止全部输出并锁存升级执行模式”，以及称重补码解析、协议重量边界、
“完整 Modbus 应答优先于轮询超时”和限位停机转换，都不依赖 STM32 寄存器，可用 Clang
在桌面直接回归。相同测试入口还检查清运进入 `page8`、`0x07` 只在同一次有效清运等待
状态下再次开锁，以及 `0x05` 被接受后才由 MCU 切回 `page0`：

`tests/uart3_atomic_batch_test.c`还直接编译真实`usart3.c`，验证普通页面、初始化和动态值批次
全有或全无进入UART3软件发送队列，队列不足时零字节发布，后续命令不能撤销已入队二维码。
该测试只证明“完整指令组已入队”，不等待USART完成、HMI回执或读回。

```powershell
cd hardware
uv run --python 3.11 --with pytest pytest -q `
  tests/test_mcu_update_execution_c.py `
  tests/test_mcu_runtime_logic_c.py
```

该测试只验证桌面逻辑和源码接线；发布前仍需用真实 STM32、香橙派 UART 和 BOOT0/NRST
接线执行硬件在环验收。

如修改 `contracts/uart/` 中的契约，还应从仓库根目录重新生成并校验 MCU C 产物：

```powershell
uv run --python 3.11 python contracts/tools/generate_contracts.py `
  --include-hardware-mcu --check
```
