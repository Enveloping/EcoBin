# EcoBin MCU fixed-frame revision 2

版本：`2.0.0`

本契约在 [`mcu-fixed-frame-v1.md`](mcu-fixed-frame-v1.md) 基础上只增加固件身份与升级
准备状态。AA、BB、CC、DD、EE、EF、F0、F1 和 A0 的方向、长度和业务含义保持不变。
不得把 revision 2 理解为通用可靠协议：它仍然没有通用 ACK、CRC、命令序号或作业恢复。

## F2 查询

固定长度 3 字节：

```text
F2 CMD F2
```

| CMD | 含义 |
| --- | --- |
| `01` | 只读查询当前固件身份和安全状态 |
| `02` | 请求进入升级准备状态 |

`02` 只有在 MCU 没有投递、清运或称重流程时才能成功。成功后 MCU 必须立即停止推杆、
释放清运电磁锁、锁定升级准备状态，并忽略新的机械业务命令直至复位。忙碌时不得中断
现有流程，只返回忙碌状态。

## F3 状态快照

固定长度 51 字节：

```text
F3 MODE STATUS PROTOCOL_REV VERSION_CODE_BE[4] VERSION_LEN
   VERSION_ASCII[32] FIRMWARE_IDENTITY[8] SAFE_FLAGS F3
```

| 字段 | 规则 |
| --- | --- |
| `MODE` | `01` 为身份查询快照，`02` 为升级准备快照 |
| `STATUS` | `00` 有效/已准备，`01` 忙碌，`02` 不安全，`03` 内部错误 |
| `PROTOCOL_REV` | 固定为 `02` |
| `VERSION_CODE_BE` | 单调 `uint32`，大端序 |
| `VERSION_LEN` | `1..32` |
| `VERSION_ASCII` | 语义版本 ASCII，未使用字节补 `00` |
| `FIRMWARE_IDENTITY` | 发布前生成的 8 字节身份，与签名 Manifest 一致 |
| `SAFE_FLAGS.bit0` | 投递与称重流程空闲 |
| `SAFE_FLAGS.bit1` | 清运流程空闲 |
| `SAFE_FLAGS.bit2` | 推杆两个方向输出均关闭 |
| `SAFE_FLAGS.bit3` | 清运电磁锁输出关闭 |
| `SAFE_FLAGS.bit4` | 升级准备状态已经锁定 |
| `SAFE_FLAGS.bit5..7` | 保留，必须为 0 |

接收方必须按固定 51 字节读取并同时校验首尾 `F3`、字段范围、ASCII 补零和保留位。
F3 只是状态快照，不确认 AA、EE 等业务命令，也不得据此补造业务完成事实。

## 部署边界

- revision 1 设备不响应 F2，只允许通过受控 SSH 的 legacy preflight 完成首次升级。
- OneNet 自动升级只允许最后一次已确认固件声明 revision 2 的设备。
- 正常业务串口为 `115200/8N1`；STM32 系统 ROM Bootloader 会话为 `115200/8E1`，
  两者不得同时打开。
- 自动升级开始前必须收到 `F2 02 F2` 对应的 `MODE=02`、`STATUS=00` 且
  `SAFE_FLAGS & 0x1F == 0x1F`；升级完成后必须重新启动应用并通过 F3 身份查询和 F1
  传感器快照。
