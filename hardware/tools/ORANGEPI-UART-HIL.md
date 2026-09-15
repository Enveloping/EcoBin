# 香橙派 UART 真机测试入口

`orangepi_uart_hil.ps1` 从 Windows 通过固定 SSH 跳板调用香橙派上的
`/root/ecobin-door-hil-20260726/uart_hil_probe.py`。它不会经过 OneNet、SQLite、
COS 或相机，适合单独验证“香橙派 ↔ MCU”边界。

在仓库根目录使用 PowerShell 7：

```powershell
# 只验证 HELLO
pwsh -File hardware/tools/orangepi_uart_hil.ps1 Hello

# 查询 MCU 完整快照
pwsh -File hardware/tools/orangepi_uart_hil.ps1 State

# 应用配置、重复 COMMIT 验证幂等，并查询最终快照
pwsh -File hardware/tools/orangepi_uart_hil.ps1 Configure `
  -RepeatConfiguration

# 真实 OPEN，等待 30 秒，再 SAFE_CLOSE
pwsh -File hardware/tools/orangepi_uart_hil.ps1 DoorCycle `
  -ConfirmPhysicalAction

# 配置幂等 + 真实门循环 + 最终快照
pwsh -File hardware/tools/orangepi_uart_hil.ps1 Full `
  -ConfirmPhysicalAction

# 任何时候单独下发 SAFE_CLOSE，并查询最终快照
pwsh -File hardware/tools/orangepi_uart_hil.ps1 SafeClose
```

也可以直接使用双击友好的 CMD 入口：

```bat
hardware\tools\orangepi-uart-hil.cmd State
hardware\tools\orangepi-uart-hil.cmd Full -ConfirmPhysicalAction
```

## 常用参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `-SerialPort` | `/dev/ttyS5` | 香橙派 UART 设备 |
| `-ConfigVersion` | 当前 Unix 毫秒 | 必须在 `1..9007199254740991` |
| `-EdgeBootId` | 当前 Unix 毫秒派生值 | 自动限制在 OneNet/JSON 安全整数范围 |
| `-DoorTravelWaitMs` | `3000` | OPEN 后现场观察等待，范围 3～45 秒 |
| `-RepeatConfiguration` | 关闭 | `Configure` 时重放相同配置并验证幂等 |
| `-RequiredCapabilities` | `0x300` | HELLO 必需能力掩码 |
| `-DryRun` | 关闭 | 只打印 SSH 命令，不连接设备 |

需要切换香橙派或联调目录时可覆盖 `-JumpHost`、`-TargetHost`、`-TargetPort` 和
`-RemoteDirectory`。脚本不保存密码、设备密钥或 OneNet/COS 凭证。

## 安全边界

- `DoorCycle` 和 `Full` 会真实打开投递门，必须显式提供
  `-ConfirmPhysicalAction`。
- `SafeClose` 只控制投递门，不适用于清运门电磁锁。
- MCU 返回 `COMMAND_DISPATCHED` 只证明 GPIO 方向已锁存；由于没有门磁，
  机械门位仍是 `NOT_OBSERVABLE`，需要现场观察。
- 若命令被中断，优先运行 `SafeClose`，再运行 `State`。
