# Linux 定长帧 PTY 模拟器使用说明

> **历史工具**：只用于fixed-frame兼容测试，不是当前UART v2 rc.26量产P7模拟器或真机证据。

## 1. 用途和边界

`fixed_frame_pty_simulator.py` 在 Linux 上创建一对伪终端（PTY），并把从端通过稳定软
链接暴露给真实的 `hardware/main.py`。它只替换 MCU，不绕过香橙派运行入口：

```text
OneNet 服务调用
  -> main.py / MQTT / SQLite / CommandProcessor
  -> FixedFrameMcuAdapter
  -> Linux PTY
  -> 虚拟 MCU
  -> F1、F3、CC、DD 或 EF
  -> main.py 生成并可靠上报事件
```

模拟器支持当前冻结协议的全部线路帧：

- 接收 `BB PRICE BB`，保存并打印 MCU 屏显价格位；
- 接收 `AA 01 AA`，延迟后返回配置的 `DD PRE POST FULL DD`；
- 接收 `EE 01 EE`，延迟后返回配置的 `EF PRE POST FULL EF`；
- 接收 `F0 01 F0`，延迟后返回配置的 `F1 VALID WEIGHT FULL SMOKE F1`；
- 接收 `F2 01 F2`，返回包含 revision 2、版本、版本码和 8 字节发布身份的 F3；
- 接收 `F2 02 F2`，先锁存升级执行状态、阻止后续业务启动，再返回配置的 F3 执行结果；
- 可让 F3 返回内部错误、部分安全位，或在 MCU 已执行后故意丢弃应答；
- 可在第一次 F1 完成后发送一次 `CC SMOKE CC`，模拟烟感状态变化；
- 接收 A0 设备入口 URL 并保存，不发送应答；
- 下行解析支持拆包、粘包、前导噪声和无效候选帧后的重新同步。

它不会模拟屏幕、按钮、门、电磁阀、限位开关、称重稳定过程、电气特性或真实执行时
序，因此不能替代最终真机 HIL。`FULL` 参数是红外原始值，不是业务满溢结论。

升级测试分成两层：PTY 层用于检查真实串口收发和 F2/F3 协议；
`mcu_firmware_upgrade_simulator.py` 则在自动化测试中另外模拟 BOOT0、NRST、STM32 ROM
Bootloader 擦写和应用重启，让真实 `McuFirmwareUpdater` 状态机完整运行。生产入口仍禁止
同时设置 `ECOBIN_MCU_SIMULATED=true` 与 `ECOBIN_MCU_UPDATE_ENABLED=true`，避免测试替身
被误当成真机烧录能力。

## 2. 前置条件

- 在 Linux 或香橙派 Linux 上执行，Windows 不提供 PTY；
- Python 3.11；
- 已在 `hardware/` 下准备真实 OneNet 配置；不要把 `.env`、设备密钥或 STS 凭证提交
  到版本库；
- 模拟器和 `main.py` 最好由同一 Linux 用户运行，以免 PTY 权限不一致。

先安装硬件侧依赖：

```bash
cd /path/to/database-refactor-onenet-edge-audit/hardware
uv sync --python 3.11
```

## 3. 启动虚拟 MCU

打开终端 A，在 `hardware/` 目录执行：

```bash
uv run --python 3.11 python tools/fixed_frame_pty_simulator.py \
  --link /tmp/ecobin-fixed-frame-mcu \
  --delivery-pre-grams 10000 \
  --delivery-post-grams 11200 \
  --delivery-full 0 \
  --delivery-result-delay-ms 40000 \
  --clean-pre-grams 11200 \
  --clean-post-grams 800 \
  --clean-full 0 \
  --clean-result-delay-ms 40000 \
  --self-test-weight-grams 10000 \
  --self-test-weight-valid 1 \
  --self-test-full 0 \
  --self-test-full-valid 1 \
  --smoke-state 0 \
  --firmware-version 1.0.0 \
  --firmware-version-code 10000 \
  --firmware-identity-hex 0102030405060708 \
  --firmware-identity-status 0 \
  --firmware-prepare-status 0 \
  --firmware-prepare-safe-flags 0x1f \
  --response-delay-ms 500
```

正常启动会输出类似：

```text
READY slave=/dev/pts/3 link=/tmp/ecobin-fixed-frame-mcu protocol=fixed-frame
```

`/dev/pts/3` 每次运行可能变化；`/tmp/ecobin-fixed-frame-mcu` 是供
`ECOBIN_SERIAL_PORT` 使用的稳定路径。模拟器退出时只会删除仍指向本次 PTY 的软链接，
不会覆盖或删除同名普通文件。

查看全部参数：

```bash
uv run --python 3.11 python tools/fixed_frame_pty_simulator.py --help
```

主要参数：

| 参数 | 含义 | 默认值 |
|---|---|---:|
| `--delivery-pre-grams` | DD 的投递前总重量 | `10000` |
| `--delivery-post-grams` | DD 的投递后总重量 | `11200` |
| `--delivery-full` | DD 红外原始值，`0` 未遮挡、`1` 遮挡 | `0` |
| `--delivery-result-delay-ms` | 收到 AA 后等待多久发送 DD | `40000` |
| `--clean-pre-grams` | EF 的清运前总重量 | `11200` |
| `--clean-post-grams` | EF 的清运后新袋皮重 | `800` |
| `--clean-full` | EF 红外原始值，`0` 未遮挡、`1` 遮挡 | `0` |
| `--clean-result-delay-ms` | 收到 EE 后等待多久发送 EF | `40000` |
| `--self-test-weight-grams` | F1 的当前总重量 | `10000` |
| `--self-test-weight-valid` | F1 重量有效标志，`0` 无效、`1` 有效 | `1` |
| `--self-test-full` | F1 红外原始值，`0` 未遮挡、`1` 遮挡 | `0` |
| `--self-test-full-valid` | F1 红外有效标志，`0` 无效、`1` 有效 | `1` |
| `--smoke-state` | F1 烟感值，`0` 正常、`1` 报警、`2` 无法读取 | `0` |
| `--smoke-change-to` | 首次 F1 后发送一次 CC，值同 `--smoke-state`；不填写则不发送 | 不发送 |
| `--firmware-version` | F3 上报的当前固件版本文本，1～32 个可打印 ASCII 字节 | `1.0.0` |
| `--firmware-version-code` | F3 上报的单调版本码，范围 `1..4294967295` | `10000` |
| `--firmware-identity-hex` | F3 上报的 8 字节发布身份，16 个小写十六进制字符且不能全零 | `0102030405060708` |
| `--firmware-identity-status` | `F2 01 F2` 的 F3 状态：`0` 成功、`1/2` 旧兼容失败、`3` 内部错误 | `0` |
| `--firmware-identity-drop-responses` | 丢弃开头多少次身份查询 F3，用于模拟超时 | `0` |
| `--firmware-prepare-status` | `F2 02 F2` 执行后的 F3 状态，取值 `0..3` | `0` |
| `--firmware-prepare-safe-flags` | 升级准备后的实际执行位；成功必须为 `0x1f` | `0x1f` |
| `--firmware-prepare-drop-responses` | MCU 仍执行并锁存升级状态，但丢弃开头多少次准备 F3 | `0` |
| `--response-delay-ms` | F1/F3 响应以及随后可选 CC 变化的延迟 | `500` |
| `--exit-after-responses` | 发出指定数量的 F1/F3/CC/DD/EF 后自动退出 | 不自动退出 |

两个重量字段的合法范围都是 `0..350000` 克。投递业务净重按 `POST - PRE` 计算；清运
移除净重仍由香橙派按已确认规则 `PRE - oldBaselineWeightGrams` 计算，不由模拟器
计算。F1 中重量或红外标为无效时，模拟器按协议把对应数据字节清零；这表示传感器
自检失败，不表示 MCU 串口通信失败。

## 4. 让真实香橙派入口连接 PTY

打开终端 B。确认 `hardware/.env` 中至少有以下串口配置：

```dotenv
ECOBIN_MCU_PROTOCOL=fixed-frame
ECOBIN_MCU_SIMULATED=true
ECOBIN_SERIAL_PORT=/tmp/ecobin-fixed-frame-mcu
ECOBIN_SERIAL_BAUDRATE=115200
ECOBIN_UART_PORT_COUNT=1
```

当前没有会放宽业务校验的全局测试模式开关。`main.py` 始终根据
`ECOBIN_MCU_PROTOCOL` 和 `ECOBIN_SERIAL_PORT` 连接所配置的串口边界；连接 PTY 时还必须
显式设置 `ECOBIN_MCU_SIMULATED=true`，使平台验收证据能够如实显示当前来源。自 V38
起该标记只用于诊断，不再影响机器验收通过或失败；当前
`config.py` 会以 `.env` 覆盖同名进程环境变量，所以已有
`.env` 时应修改其中的 `ECOBIN_SERIAL_PORT`，不能只在 shell 中临时 `export`。

其余 OneNet、部署和 COS 配置继续使用目标设备的真实测试环境值。然后启动真实入口：

```bash
uv run --python 3.11 python main.py
```

仓库中的 `ecobin-mcu-simulator.service` 是对应的 systemd 运行单元，显式配置健康 F1：
重量 10000 克、重量/红外有效位均为 1、红外未遮挡、烟感正常。该单元不配置
`--smoke-change-to`，避免正式常驻模拟器启动后主动制造报警或传感器故障；CC 变化使用
隔离 PTY 测试验证。当前常驻单元把 DD 投递结果和 EF 清运结果都延迟 40 秒，用于模拟
用户实际操作耗时；F0 自检仍只使用 500 毫秒通用延迟，因此不会因业务等待影响启动自检。
常驻单元还显式上报 revision 2、版本 `1.0.0`、版本码 `10000` 和测试发布身份，并让
F2 模式 02 返回成功的 `SAFE_FLAGS=1F`；这些值只属于虚拟 MCU，不得登记为真实生产固件。

本测试只替换 MCU。无真实摄像头时，可以把
`ECOBIN_CAMERA_OUTSIDE`、`ECOBIN_CAMERA_INSIDE` 分别设为
`simulated://outside`、`simulated://inside`；它们会生成可解码且哈希不同的占位
JPEG，不需要全局模式、OpenCV 或 V4L2。详细边界见
[`SIMULATED-CAMERA.md`](SIMULATED-CAMERA.md)。摄像头不存在、拍照失败或上传暂未完成
时，应记录失败或待上传状态但不阻止 AA/EE；完成事件中尚未上传好的图片 URL 保持
空值，后续由 `photoStatusReported` 补报。

## 5. 跑完整 OneNet 链路

### 5.0 启动自检

`main.py` 打开串口后会先发送一次 F0。终端 A 应看到：

```text
RX SELF_TEST frame=F0 01 F0
TX SELF_TEST_RESULT frame=F1 ... validFlags=03 weightGrams=10000 infraredBlocked=0 smoke=0
```

合法 F1 会成为启动运行快照的重量、红外和烟感事实。若 F1 超时、非法，或有效标志
表明称重/红外不可用，香橙派仍连接 OneNet 并上报降级状态，但拒绝新的投递和清运。
`--smoke-state 2` 模拟“MCU 在线但烟感无法读取”；它与完全没有 F1 的串口超时不同。
正式机器验收每次也会发送新的 F0，不复用启动时或上一次验收的旧结果。

### 5.1 固件身份与升级准备协议

香橙派查询身份时，终端 A 应看到：

```text
RX FIRMWARE_IDENTITY frame=F2 01 F2 mode=1 prepared=False
TX FIRMWARE_IDENTITY_RESULT frame=F3 ... mode=1 status=0 revision=2 version=1.0.0 versionCode=10000 identity=0102030405060708 safeFlags=0x0F
```

香橙派已经完成本地准入并发送升级准备命令时，终端 A 应看到：

```text
RX FIRMWARE_PREPARE frame=F2 02 F2 mode=2 prepared=True
TX FIRMWARE_PREPARE_RESULT frame=F3 ... mode=2 status=0 revision=2 version=1.0.0 versionCode=10000 identity=0102030405060708 safeFlags=0x1F
```

第二帧只证明 MCU 已执行“停止活动、关闭输出并锁存升级状态”，是否允许升级、是否进入
ROM Bootloader 仍由香橙派状态机决定。锁存后，模拟 MCU 会像真机协议要求一样忽略新的
AA、BB、EE 和 A0；重启模拟器相当于 MCU 应用复位并清除该内存锁存状态。

常用故障启动参数如下：

| 要模拟的事实 | 参数 |
|---|---|
| 身份帧明确报告内部错误 | `--firmware-identity-status 3` |
| 前两次身份查询超时 | `--firmware-identity-drop-responses 2` |
| 准备命令执行不完整 | `--firmware-prepare-status 3 --firmware-prepare-safe-flags 0x0f` |
| MCU 已锁存准备状态，但第一帧应答在串口上丢失 | `--firmware-prepare-drop-responses 1` |

这些参数测试的是应用 UART 协议。真实升级器的 BOOT0/NRST、擦写、目标验证和回滚模拟见
第 6 节；不要为 PTY 进程关闭生产配置中的“模拟 MCU 禁止烧录”保护。

### 5.2 投递

1. 等待终端 B 显示 MQTT 已连接和启动完成。
2. 从现有后端或 OneNet 控制台调用 `startDeliverySession`。载荷必须使用合法的
   `commandUid`、`sessionUid`、当前配置身份、`portNo=1` 和尚未过期的命令封套。
3. 终端 A 应依次看到：

   ```text
   RX PRICE frame=BB 04 BB digit=4 displayYuanPerKg=0.4
   RX DELIVERY_START frame=AA 01 AA
   TX DELIVERY_START_RESULT frame=DD ... preGrams=10000 postGrams=11200 infraredBlocked=0
   ```

4. 终端 B 应先记录服务命令已经受理，随后处理
   `COMPAT_DELIVERY_RESULT`，生成并通过 OneNet 上报 `deliveryComplete`。
5. 在 OneNet 控制台或后端确认最终事件。服务同步回执只表示香橙派已经接收并保存
   命令，不表示投递已经完成；`deliveryComplete` 才是完成事实。

### 5.3 清运

先确认投递已完成、唯一工作槽已经释放，再执行：

1. 从现有后端或 OneNet 控制台调用 `startCleanOperation`。载荷应包含合法
   `operationUid`、`newBagUid`、当前配置身份、`portNo=1`；需要验证移除净重时还应
   携带测试所需的 `oldBaselineWeightGrams`。
2. 终端 A 应看到：

   ```text
   RX CLEAN_START frame=EE 01 EE
   TX CLEAN_START_RESULT frame=EF ... preGrams=11200 postGrams=800 infraredBlocked=0
   ```

3. 终端 B 应处理 `COMPAT_CLEAN_RESULT`，保存 `POST=800` 为新袋皮重，按
   `PRE - oldBaselineWeightGrams` 计算移除净重，并上报 `cleanComplete`。
4. 在 OneNet 控制台或后端确认最终事件。

若终端 A 一直没有 `RX`，先检查服务是否被边缘校验拒绝，再核对 `.env` 中的
`ECOBIN_MCU_PROTOCOL` 和 `ECOBIN_SERIAL_PORT`。若已有其他活动投递或清运，新的
启动服务会被唯一工作槽规则拒绝，也不会出现串口帧。

## 6. 自动化验证

只运行模拟器测试：

```bash
uv run --python 3.11 --with pytest \
  pytest -q tests/test_fixed_frame_pty_simulator.py
```

测试包含：

- 与操作系统无关的拆包、粘包、噪声重同步和精确帧编码；
- 价格保存、F1/F3/DD/EF 自动响应、CC 状态变化和参数边界；
- F2 身份查询、升级准备锁存、明确错误、部分安全位和已执行但应答丢失；
- Linux 上使用真实 `pty.openpty()`、PySerial 和
  `FixedFrameMcuAdapter` 的 F0/F1、投递和清运双向集成测试。

完整升级状态机模拟不依赖 Linux PTY，因此 Windows 和 Linux 都能运行：

```bash
uv run --python 3.11 --with pytest \
  pytest -q tests/test_mcu_firmware_upgrade_simulator.py
```

每个用例都会临时生成已签名的稳定包和目标包，并组合真实的 `EdgeStore`（香橙派 SQLite
升级日志和维护锁）、`FixedFrameMcuAdapter`、`FirmwarePackageCache` 与
`McuFirmwareUpdater`。只有 UART 对端、BOOT0/NRST 和 `stm32flash` 边界由模拟器代替，
所以断言覆盖的是实际升级状态机，而不是另写的一套简化流程。

升级模拟器还会执行严格 ROM 准入：MCU 正在运行应用且 F2 锁存已被复位清除时，直接操作
BOOT0/NRST 会返回 `SIMULATED_PREPARE_REQUIRED`。测试夹具只为首次本地 revision 1 → 2
迁移保留一次显式例外；此后的目标重试和回滚必须各自重新完成 F2。若 `stm32flash` 在
ROM 内失败，则 MCU 没有启动应用，可以直接重试而不发送无法解析的应用帧。

| 场景 | 单独执行时使用的 `-k` 关键字 | 应观察到的终态 |
|---|---|---|
| 正常目标升级 | `completes_an_upgrade` | `SUCCEEDED`，新身份成为稳定固件 |
| F3 身份明确报错 | `identity_error_rejects` | `REJECTED`，不发送 F2 准备、不擦写、解除维护锁 |
| F2 准备明确失败 | `prepare_error_is_reset` | 复位并复核原身份/F1 后 `REJECTED` |
| F2 已执行但 F3 丢失 | `dropped_prepare_response` | 复位并复核原身份/F1 后 `REJECTED` |
| F2 失败后的应用恢复也失败 | `prepare_recovery_failure` | `FAILED_LOCKED`，继续阻止投递和清运 |
| 新固件 F1 自检失败 | `self_test_failure` | 自动恢复上一稳定固件，`ROLLED_BACK` |
| 新固件 F3 身份与签名包不符 | `identity_mismatch` | 自动恢复上一稳定固件，`ROLLED_BACK` |
| 第二次 F2 执行失败 | `repeated_prepare_failure` | 复位核对当前应用后 `FAILED_LOCKED`，不再操作 BOOT0 |
| 应用启动结果不确定 | `unknown_mode_after_application_boot` | `FAILED_LOCKED`，不猜测 MCU 仍在 ROM |
| 第二次 F2 后进程退出 | `restart_after_repeated_prepare` | 重启后先复位验证并重新 F2，最终安全回滚 |
| 目标固件连续三次擦写失败 | `flash_failure_exhausts` | 回滚成功，`ROLLED_BACK` |
| 目标和回滚都连续擦写失败 | `target_and_rollback` | `FAILED_LOCKED`，维护锁保留 |

例如只验证“目标与回滚都失败后不能恢复业务”：

```bash
uv run --python 3.11 --with pytest pytest -q \
  tests/test_mcu_firmware_upgrade_simulator.py -k target_and_rollback
```

在 Windows 上，前两类测试正常执行，真实 PTY 集成用例会明确跳过。完整硬件侧回归：

```bash
uv run --python 3.11 --with pytest pytest -q
```

## 7. 停止和恢复真机

两个终端都使用 `Ctrl+C` 停止。模拟器会清理
`/tmp/ecobin-fixed-frame-mcu` 软链接。恢复真实 MCU 前，把 `.env` 的
`ECOBIN_SERIAL_PORT` 改回实际串口，例如 `/dev/ttyS5`，再重新启动 `main.py`。
