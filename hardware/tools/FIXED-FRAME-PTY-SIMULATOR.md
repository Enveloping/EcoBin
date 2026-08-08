# Linux 定长帧 PTY 模拟器使用说明

## 1. 用途和边界

`fixed_frame_pty_simulator.py` 在 Linux 上创建一对伪终端（PTY），并把从端通过稳定软
链接暴露给真实的 `hardware/main.py`。它只替换 MCU，不绕过香橙派运行入口：

```text
OneNet 服务调用
  -> main.py / MQTT / SQLite / CommandProcessor
  -> FixedFrameMcuAdapter
  -> Linux PTY
  -> 虚拟 MCU
  -> F1、CC、DD 或 EF
  -> main.py 生成并可靠上报事件
```

模拟器支持当前冻结协议的全部线路帧：

- 接收 `BB PRICE BB`，保存并打印 MCU 屏显价格位；
- 接收 `AA 01 AA`，延迟后返回配置的 `DD PRE POST FULL DD`；
- 接收 `EE 01 EE`，延迟后返回配置的 `EF PRE POST FULL EF`；
- 接收 `F0 01 F0`，延迟后返回配置的 `F1 VALID WEIGHT FULL SMOKE F1`；
- 可在第一次 F1 完成后发送一次 `CC SMOKE CC`，模拟烟感状态变化；
- 下行解析支持拆包、粘包、前导噪声和无效候选帧后的重新同步。

它不会模拟屏幕、按钮、门、电磁阀、限位开关、称重稳定过程、电气特性或真实执行时
序，因此不能替代最终真机 HIL。`FULL` 参数是红外原始值，不是业务满溢结论。

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
  --delivery-post-grams 12500 \
  --delivery-full 0 \
  --clean-pre-grams 12500 \
  --clean-post-grams 800 \
  --clean-full 0 \
  --self-test-weight-grams 10000 \
  --self-test-weight-valid 1 \
  --self-test-full 0 \
  --self-test-full-valid 1 \
  --smoke-state 0 \
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
| `--delivery-post-grams` | DD 的投递后总重量 | `12500` |
| `--delivery-full` | DD 红外原始值，`0` 未遮挡、`1` 遮挡 | `0` |
| `--clean-pre-grams` | EF 的清运前总重量 | `12500` |
| `--clean-post-grams` | EF 的清运后新袋皮重 | `800` |
| `--clean-full` | EF 红外原始值，`0` 未遮挡、`1` 遮挡 | `0` |
| `--self-test-weight-grams` | F1 的当前总重量 | `10000` |
| `--self-test-weight-valid` | F1 重量有效标志，`0` 无效、`1` 有效 | `1` |
| `--self-test-full` | F1 红外原始值，`0` 未遮挡、`1` 遮挡 | `0` |
| `--self-test-full-valid` | F1 红外有效标志，`0` 无效、`1` 有效 | `1` |
| `--smoke-state` | F1 烟感值，`0` 正常、`1` 报警、`2` 无法读取 | `0` |
| `--smoke-change-to` | 首次 F1 后发送一次 CC，值同 `--smoke-state`；不填写则不发送 | 不发送 |
| `--response-delay-ms` | 收到 AA/EE/F0 后发送对应结果的延迟 | `500` |
| `--exit-after-responses` | 发出指定数量的 F1/CC/DD/EF 后自动退出 | 不自动退出 |

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
隔离 PTY 测试验证。

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

### 5.1 投递

1. 等待终端 B 显示 MQTT 已连接和启动完成。
2. 从现有后端或 OneNet 控制台调用 `startDeliverySession`。载荷必须使用合法的
   `commandUid`、`sessionUid`、当前配置身份、`portNo=1` 和尚未过期的命令封套。
3. 终端 A 应依次看到：

   ```text
   RX PRICE frame=BB 04 BB digit=4 displayYuanPerKg=0.4
   RX DELIVERY_START frame=AA 01 AA
   TX DELIVERY_START_RESULT frame=DD ... preGrams=10000 postGrams=12500 infraredBlocked=0
   ```

4. 终端 B 应先记录服务命令已经受理，随后处理
   `COMPAT_DELIVERY_RESULT`，生成并通过 OneNet 上报 `deliveryComplete`。
5. 在 OneNet 控制台或后端确认最终事件。服务同步回执只表示香橙派已经接收并保存
   命令，不表示投递已经完成；`deliveryComplete` 才是完成事实。

### 5.2 清运

先确认投递已完成、唯一工作槽已经释放，再执行：

1. 从现有后端或 OneNet 控制台调用 `startCleanOperation`。载荷应包含合法
   `operationUid`、`newBagUid`、当前配置身份、`portNo=1`；需要验证移除净重时还应
   携带测试所需的 `oldBaselineWeightGrams`。
2. 终端 A 应看到：

   ```text
   RX CLEAN_START frame=EE 01 EE
   TX CLEAN_START_RESULT frame=EF ... preGrams=12500 postGrams=800 infraredBlocked=0
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
- 价格保存、F1/DD/EF 自动响应、CC 状态变化和参数边界；
- Linux 上使用真实 `pty.openpty()`、PySerial 和
  `FixedFrameMcuAdapter` 的 F0/F1、投递和清运双向集成测试。

在 Windows 上，前两类测试正常执行，真实 PTY 集成用例会明确跳过。完整硬件侧回归：

```bash
uv run --python 3.11 --with pytest pytest -q
```

## 7. 停止和恢复真机

两个终端都使用 `Ctrl+C` 停止。模拟器会清理
`/tmp/ecobin-fixed-frame-mcu` 软链接。恢复真实 MCU 前，把 `.env` 的
`ECOBIN_SERIAL_PORT` 改回实际串口，例如 `/dev/ttyS5`，再重新启动 `main.py`。
