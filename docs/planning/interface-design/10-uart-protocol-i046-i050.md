# EcoBin P0 目标接口设计：香橙派—MCU UART 正式协议（I-046～I-050）

> [!IMPORTANT]
> 2026-08-02：`SAMPLE_FULLNESS` 不再由后端正常下发。香橙派使用投递结束/清运完成时的最终设备观察计算当前袋状态，并只在状态变化时向云端上报；fixed-frame 兼容路径必须标记缓存观察来源。本文下方主动采样描述仅保留为历史协议能力，现行准入见 [`../../architecture/fullness-reporting-v25.md`](../../architecture/fullness-reporting-v25.md)。

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-046～I-050 规范模型已确认；2026-07-24 已按投递会话一单及清运电磁阀实际能力修订；2026-07-27 增加现有 MCU 固定帧适配配置**
>
> 说明：本文件定义香橙派内部规范 MCU 模型及 `uart-v1` 线路契约，包括二进制帧、版本握手、ACK/NACK、幂等重试、设备状态机、称重/传感器语义及重启恢复。它承接 I-016～I-030 的配置、投递、清运和满溢业务语义，以及 I-041～I-045 的 OneNet、边缘 SQLite 和业务确认边界，不修改那些上游状态机。
>
> 适配边界：2026-07-27 项目负责人确认现有 MCU 不再原生实现 `uart-v1`。F-11 在香橙派
> 硬件边界以单一显式 `fixed-frame` 模式适配已确定的 `AA/BB/EE` 下行和 `DD/EF`
> 上行；它必须映射为本章规范业务事实，并将无法表达的能力标为未知/不支持。旧 D1、
> 自动探测、双解析和按消息失败回退仍禁止。固定帧逐字节定义以
> [`ecobin-mcu-fixed-frame-v2` 完整单文件协议](../../../hardware/docs/单片机-香橙派适配通信协议详细内容.md)
> 为准。

## 本章统一边界

1. P0 物理链路默认采用 `115200 baud、8 data bits、no parity、1 stop bit、no flow control`。串口设备路径可以按香橙派部署环境配置，但两端不能通过业务消息动态改变串口参数。
2. MCU 负责屏幕与按钮状态机、投递门真实门控、清运电磁阀通断、传感器采样/滤波/消抖、稳定称重、命令去重、看门狗和投递门独立超时关门；清运门由电磁阀通电解锁后自动弹出、由清运员手动关闭，且没有门磁。香橙派负责可靠保存业务上下文、在线授权、作业编排、照片、基准/净重计算、OneNet/COS 以及重启恢复。
3. 协议明确区分三类身份：

   | 身份 | 作用域 | 用途 |
   |---|---|---|
   | `sessionUid/operationUid/detectionUid/measurementUid` 与云端 `commandUid` | 业务作业 | 关联后端已经授权的投递会话、清运、检测和配置事实；投递不再分配会话内第二作业身份 |
   | `mcuCommandUid` | 一次香橙派到 MCU 的稳定硬件命令 | MCU 幂等执行；直接实现云端命令时可以复用其 `commandUid`，操作内本地子动作由香橙派先写 SQLite 再生成 |
   | `txSequence`、`mcuBootId + mcuEventSequence` | UART 传输与 MCU 观察 | 分别用于一次方向内传输确认和 MCU 事件去重，不能替代业务作业身份 |

4. `txSequence` 成功、UART ACK、门动作完成、香橙派事件落盘、OneNet 接受、后端业务确认仍是不同事实。任何前一层成功都不能伪装成后一层成功。
5. UART 不传租户、机构、用户、钱包、审核结果或返现终态。MCU 只接收完成现场动作所需的最小配置和作业身份；单价只用于本投递会话屏幕展示，不使 MCU 成为金额权威。
6. `uart-v1` 载荷采用按消息类型固定的强类型二进制结构，不使用逗号文本、JSON、浮点数、任意键值 Map 或反射式通用命令。所有字段必须由同一份机器可读注册表生成或校验 C/Python 编解码器。
7. 规范协议版本为 `1.0`。本章冻结线级语义；消息类型数值、逐字段偏移、生成代码和黄金字节样本不得改变本章的字段单位、状态和恢复边界。
8. `fixed-frame` 是当前现有 MCU 的部署适配配置，不是第二份云端契约，也不宣称具备
   `uart-v1` 的 CRC、ACK、命令身份、状态查询、故障枚举或非易失能力。两种模式在同一
   进程实例中互斥。

## I-046 二进制帧、严格分帧与版本握手

**已确认：正式 UART 使用有魔数、版本、消息类型、载荷长度、方向内序号和 CRC16 的有界二进制帧；启动必须先交换协议、固件、启动代际、投口数和能力，没有共同主版本时禁止任何作业。**

### 1. P0 帧布局

所有多字节整数使用网络字节序（big-endian）。一个完整帧最多 `256` 字节：

| 偏移 | 长度 | 字段 | P0 规则 |
|---:|---:|---|---|
| `0` | 2 | `magic` | 固定 `0xEC 0x42` |
| `2` | 1 | `protocolMajor` | P0 固定 `1` |
| `3` | 1 | `protocolMinor` | P0 固定 `0` |
| `4` | 1 | `messageType` | 机器注册表中的固定枚举 |
| `5` | 1 | `flags` | bit 0 为 `ACK_REQUIRED`；bit 1～7 在 1.0 中必须为 0 |
| `6` | 2 | `payloadLength` | `0..242`，只表示 payload 长度 |
| `8` | 4 | `txSequence` | 当前发送方启动代际内的正 `uint32` |
| `12` | N | `payload` | 消息类型专属固定结构 |
| `12+N` | 2 | `crc16` | 覆盖偏移 `2` 至 payload 末尾，不包含 magic 和 CRC 本身 |

因此：

```text
totalFrameLength = 14 + payloadLength
totalFrameLength <= 256
```

除 `ACK/NACK/HELLO/HELLO_ACK` 外，P0 命令和事件都必须设置 `ACK_REQUIRED`；`ACK/NACK/HELLO_ACK` 的 flags 固定为 0，HELLO 通过专属 `HELLO_ACK` 完成握手。发送方不能自行把注册表规定的关键事件降级为无需确认。

P0 CRC 精确定义为 `CRC-16/CCITT-FALSE`：

```text
poly   = 0x1021
init   = 0xFFFF
refin  = false
refout = false
xorout = 0x0000
```

该变体的标准检查值固定为：

```text
CRC16-CCITT-FALSE(ASCII "123456789") = 0x29B1
```

实现不得把名称相近但参数不同的 XMODEM、KERMIT 或 X.25 变体当成同一种 CRC。下一阶段必须为 C 与 Python 生成相同的空载荷、普通载荷、包含 magic 字节载荷及最大帧黄金向量。

CRC 只检测线路噪声和意外损坏，不提供密码学认证或防恶意篡改。P0 假设 UART 位于受控机箱内部；如果以后把“攻击者可接触并注入本地总线”纳入威胁模型，必须单独设计带密钥认证的新协议版本，不能把 CRC 或未加密 SHA-256 宣称为安全签名。

### 2. 载荷公共编码

- UUID 以 RFC 4122 网络顺序的原始 16 字节传输，不发送带连字符字符串。
- SHA-256 以原始 32 字节传输，不发送十六进制字符串。
- 布尔值只允许单字节 `0/1`；其他值为字段错误。
- 枚举按注册表使用无符号 8 位或 16 位整数；未知值不得默认为某个已知状态。
- 重量一律为带符号 `int32` 克；价格一律为无符号整数的“元/千克 × 10000”。
- 相对时长使用 `uint32` 毫秒；MCU 发生时间使用启动代际内的单调 `uint64 uptimeMs`。UART 不要求 MCU 维护 UTC。
- 固件版本等诊断短字符串采用 `uint8 byteLength + UTF-8 bytes`，长度上限由消息 Schema 固定；禁止 NUL 结尾和未限定长度字符串。
- 固定结构中的保留位、保留字节在发送时必须为 0，接收时非 0 视为不支持的协议扩展，不能静默猜测。

### 3. 严格解析与重同步

接收器按下列规则处理任意拆包、粘包和噪声：

1. 在有界缓冲区中扫描完整 magic；magic 前的噪声逐字节丢弃并累计诊断计数。
2. 收满 12 字节头后只做形成有界候选帧所需的 `payloadLength <= 242` 检查；CRC 通过前不相信版本、flags、类型或序号。长度非法时只丢弃当前 magic 的第一个字节，再继续搜索，不能相信错误长度跳过后续有效帧。
3. 合法长度的候选帧在 100 ms 帧内接收期限内等待完整字节；期限届满仍不完整时同样丢弃当前 magic 的第一个字节并重同步。
4. 完整候选帧 CRC 错误时不解析 payload、不更新序号、不发送可能错误关联的 NACK，只记录诊断并从当前 magic 的下一个字节继续扫描。
5. CRC 正确后才校验协议版本、flags、消息类型、该类型的精确 payload 长度、字段范围、状态与身份。
6. 接收缓冲区硬上限为 512 字节。超过上限时丢弃不可能构成一个合法帧的最旧字节，只保留可能作为 magic 前缀的末尾 `0xEC`；不得因 MCU 持续噪声造成无界内存增长。

magic 可以自然出现在 payload 中；合法长度和 CRC 决定帧边界，不使用转义字节，也不以换行或再次出现 magic 提前截断当前候选帧。

### 4. HELLO 与版本协商

串口打开、任一端进程/固件重启或链路被判定重新建立后，两端首先发送 `HELLO`。载荷至少包含：

```text
senderRole
senderBootId
supportedMajor
minimumMinor
maximumMinor
firmwareIdentity
firmwareVersion
portCount
capabilityBitmap
maximumFrameLength
```

规则如下：

- `senderBootId` 是本端本次启动的不透明非零 `uint64`，同一启动期固定且每次真正重启必须改变。MCU 优先使用持久单调启动计数器；只有具备足够熵时才可使用随机值。香橙派进程使用新的随机启动 ID。
- P0 两端只宣告 major `1`、minor `0`。同一 major 下以后可以选择双方共同支持的最高 minor；minor 协商不能绕过某条消息所需 capability。
- `portCount` 必须与设备资产预期投口数一致，且每条投口消息的 `portNo` 必须落在 `1..portCount`。
- 双方完成 `HELLO/HELLO_ACK`、确认相同主版本和全部 P0 必需能力后，UART 状态才可进入 `READY`。握手前除 `HELLO/HELLO_ACK/NACK` 外的消息不得执行。
- 没有共同 major、帧上限低于 256、投口数不一致或缺少当前配置所需能力时，链路进入 `INCOMPATIBLE`：保持门安全、拒绝新投递/清运/检测，周期性重试 HELLO，并由香橙派形成设备故障事实。
- `txSequence` 在每个发送方向、每个 `senderBootId` 下从 1 单调递增，0 保留。P0 禁止同一启动代际内回绕；达到上限前必须停止新作业并以新的启动代际重新握手。

## I-047 ACK/NACK、重试和幂等执行

**已确认：ACK 只确认对应消息已按该接收方规则可靠受理，不代表物理动作完成；同一命令重试复用相同硬件命令身份和 UART 序号，重复命令不得再次驱动电机，关键 MCU 事件必须重发到香橙派 SQLite 已提交。**

### 1. P0 停等传输

P0 每个方向最多保留一个等待 ACK 的帧；两个方向可以同时各有一个。`ACK_REQUIRED` 帧使用：

```text
ACK timeout       = 500 ms
maximum sends     = 3（首次发送 + 最多 2 次原帧重发）
retry identity    = 同一完整帧、同一 txSequence、同一 payload 和 CRC
```

三次均未得到可验证 ACK/NACK 时，发送方只得到 `UART_ACK_TIMEOUT`，物理结果保持未知：

- 不得仅为“再试一次”生成新的业务作业或新的 `mcuCommandUid`；
- 香橙派先执行 `QUERY_STATE`、读取待发事件或进入安全恢复；
- 涉及可能打开的投递门时优先收敛 `SAFE_CLOSE`；涉及清运门时只能先使电磁阀断电并进入原清运员恢复，`SAFE_CLOSE` 不适用于清运门。两者都不能把“没收到 ACK”解释成“MCU 没执行”；
- 链路持续失败时停止新作业并产生聚合故障。

最终超时后到达的迟到 ACK 只追加传输诊断，不能使已经退出等待态的 MCU 突然开门，也不能使香橙派跳过状态查询直接宣布命令成功；后续动作必须基于当前状态机和仍有效的显式授权。

“最多三次发送”限制一次传输 burst，不允许命令层无界猛发。MCU 关键事件在一个 burst 失败后仍保留于持久队列；链路恢复或退避后可以使用新的 `txSequence` 发起下一 burst，但必须复用原 `(mcuBootId, mcuEventSequence)` 和完全相同的事件内容，直到获得持久 ACK 或进入显式人工隔离。

### 2. ACK 与 NACK 语义

`ACK` 至少引用对端当前 boot、`referencedTxSequence`、`referencedMessageType` 和处置结果：

```text
ACCEPTED
DUPLICATE_ACCEPTED
```

`NACK` 至少引用可可信解析的序号/类型并返回稳定错误码：

```text
UNSUPPORTED_VERSION
UNSUPPORTED_MESSAGE
UNSUPPORTED_FLAGS
INVALID_LENGTH
INVALID_FIELD
EXPIRED
BUSY
STATE_CONFLICT
UNKNOWN_WORK
IDEMPOTENCY_CONFLICT
SAFETY_BLOCKED
INTERNAL_FAULT
```

- CRC 错、头部尚不足或 magic 噪声不发送 NACK，因为关联序号不可信。
- `ACK` 一个香橙派命令只表示 MCU 已校验并登记该命令；投递门真实状态、清运电磁阀通断、稳定重量和执行失败必须由后续各自的强类型事件表达。清运锁事件不得命名或解释为真实 `DOOR_OPENED/DOOR_CLOSED`。
- `ACK` 一个 MCU 关键事件只允许在香橙派把原始事件身份、摘要和强类型内容提交到 SQLite 后发送。只放入内存队列不能 ACK。
- `BUSY/STATE_CONFLICT/SAFETY_BLOCKED` 是真实状态拒绝，不由串口层原样自动重发；香橙派必须先根据当前作业和状态快照决定下一步。
- `UNSUPPORTED_*`、`IDEMPOTENCY_CONFLICT` 或反复 `INTERNAL_FAULT` 锁存协议/安全故障，不能通过增加重试次数掩盖。

### 3. 命令幂等

每条可能改变 MCU 状态的命令包含：

```text
mcuCommandUid
commandDigestSha256
workType + workUid
portNo
command-specific payload
```

`commandDigestSha256` 覆盖消息类型、作业目标和稳定语义字段，不覆盖 `txSequence`、重试次数或诊断时间。规则如下：

1. 第一次合法命令：MCU 先登记 `mcuCommandUid + digest + 当前处置`，再返回 `ACCEPTED`。
2. 同一 ID、同一摘要重复：返回 `DUPLICATE_ACCEPTED` 及已有进度/结果，不重新开门、关门、测量或应用配置。
3. 同一 ID、不同摘要：返回 `IDEMPOTENCY_CONFLICT`，不采用“最后写入覆盖”。
4. 同一 UART 帧因 ACK 丢失而重传，以及链路重建后香橙派使用新 `txSequence` 重新询问同一命令，均服从同一 `mcuCommandUid` 去重。
5. MCU 至少保留当前作业和本启动代际已终结命令的有界去重记录；配置版本/摘要和安全所需终态必须持久化跨重启。记录回收不能让仍可能重放的旧开门命令重新执行。

直接对应某条云端设备命令时，`mcuCommandUid` 复用其 `commandUid`。清运操作内允许离线执行的再次解锁等本地子动作，由香橙派在 SQLite 中先生成并持久化新的 `mcuCommandUid`，同时保存原 `operationUid` 和父命令关系，之后才发送 MCU。

### 4. MCU 关键事件

MCU 事件使用 `(mcuBootId, mcuEventSequence)` 作为硬件侧稳定身份：

- `mcuEventSequence` 是每个 MCU 启动代际内共享的正 `uint32`，所有关键事件类型共用一个序号空间；
- 同一启动代际内禁止序号回绕；达到上限前必须停止新作业、保存全部待确认事件并以新 boot 代际安全重启；
- 同一身份、同一内容重复时香橙派返回原 ACK；同一身份不同内容锁存 `IDEMPOTENCY_CONFLICT`；
- 投递门实际打开/关闭、清运电磁阀通断、开门/解锁前和最终关门/确认时稳定重量、本地按钮选择、配置结果、满溢采样、基准测量、严重故障及投递门安全关门结果都属于关键事件；
- MCU 在收到香橙派持久 ACK 前不得从待发队列删除事件，并按相同事件身份重发；
- 作为某个不可逆物理边界唯一证据的未确认事件必须有可跨看门狗/掉电的非易失保存或等价恢复证据。MCU 重启后先通过 HELLO 报告待确认事件数量，再按原身份补发；存储能力不足不能以静默丢事件降级。

香橙派把 MCU 事件可靠归并到原本地作业，再生成 I-041 的独立 `eventUid + edgeEventSequence`。MCU 序号不是 OneNet 事件序号，后端也不能用它生成订单身份。

## I-048 MCU 高层命令与投递/清运状态机

**修订后确认：投递以 `sessionUid` 绑定整场，继续按钮只触发本地再次开门；清运门只上报电磁阀通断，不存在清运门物理开关事件。**

### 1. P0 高层命令注册表

| 命令 | 目标 | MCU 核心职责 |
|---|---|---|
| `APPLY_CONFIG` | `applicationUid` | 校验完整配置身份、MCU 子集摘要和参数范围，持久应用 MCU 所需配置并返回精确证明 |
| `START_DELIVERY_SESSION` | `sessionUid` | 绑定整场投递，取得首次开门前稳定总重量，执行一个或多个本地开关门轮次，最终只产生一份整场结果 |
| `START_CLEAN_OPERATION` | `operationUid` | 绑定首次清运执行并取得首次解锁前稳定总重量；不以 ACK 冒充已经解锁 |
| `UNLOCK_CLEAN_DOOR` | 原 `operationUid` | 给清运电磁阀通电解锁；首次和每次再次解锁都使用新的 `mcuCommandUid`，但不创建新业务操作 |
| `RESUME_CLEAN_OPERATION` | 原 `operationUid` | 加载经后端重新授权的原清运操作和新执行窗口；本命令本身不自动通电解锁 |
| `END_CLEAN_BEFORE_UNLOCK` | 原 `operationUid` | 仅在电磁阀从未通电且无在途解锁命令时结束已准备操作 |
| `SAMPLE_FULLNESS` | `detectionUid + sampleRole` | 在投递门关闭、清运锁断电且无冲突动作时按配置等待、采集红外和稳定总重量并返回各来源健康 |
| `MEASURE_BASELINE` | `measurementUid` | 在工作人员已确认当前袋为空的授权下采集一次稳定总重量；不自行写业务基准 |
| `QUERY_STATE` | 当前链路 | 不驱动执行器，返回 MCU 启动代际、当前作业、投递门、清运锁、传感器健康和待确认事件摘要 |
| `SAFE_CLOSE` | 整机或指定投口 | 只作用于具有真实门控的投递门；不建立业务完成事实，返回逐投递门真实结果 |

OneNet 到 UART 的名称映射固定为：`APPLY_CONFIGURATION → APPLY_CONFIG`、`START_DELIVERY_SESSION → START_DELIVERY_SESSION`、`MEASURE_EMPTY_BAG_BASELINE → MEASURE_BASELINE`、`END_CLEAN_BEFORE_UNLOCK → END_CLEAN_BEFORE_UNLOCK`，其余同名。`UNLOCK_CLEAN_DOOR` 是香橙派在原清运操作内先写 SQLite 后生成的本地子命令，不新增云端作业。`QUERY_STATE/SAFE_CLOSE` 是边缘恢复命令；`SAFE_CLOSE` 明确不适用于清运门，安全初始化只能让清运电磁阀断电。

命令不能使用“动作名称 + 投口号”裸载荷。所有命令都必须包含 `mcuCommandUid + commandDigestSha256`；作业命令再包含强类型 `workUid`、`portNo`、配置/授权摘要及相对执行期限。过期命令返回 `EXPIRED`，不能先执行再报告过期。

UART 层不提供通用分片。完整 MCU 配置若超过单帧 242 字节 payload，`APPLY_CONFIG` 逻辑命令按注册表拆为 `BEGIN → DEVICE_BLOCK/逐投口 PORT_BLOCK → COMMIT`：

- 所有分段共享 `applicationUid + version + contentSha256 + mcuPayloadSha256`；每个分段使用香橙派预先持久化的独立 `mcuCommandUid` 和固定 `partIndex/partCount`；
- MCU 把分段写入非活动持久 staging 区并在该分段提交后才 ACK；重复同一分段幂等，缺段、同序号异内容或总摘要不符均不能应用；
- 只有 `COMMIT` 校验全部分段后，才原子切换当前 MCU 配置，并在 `CONFIG_APPLY_RESULT` 中返回完整版本和摘要；
- 传输中断或重启时继续使用旧已应用配置，HELLO/QUERY_STATE 报告 staging 进度，不能把半份配置标为 `APPLIED`。

### 2. 投递会话

同一 `sessionUid` 的 MCU 阶段可以在本地重复开关门，但云端仍是一场：

```text
IDLE
  → DELIVERY_SESSION_PREPARING
  → DELIVERY_FIRST_PREOPEN_MEASURING
  → DELIVERY_OPENING
  → DELIVERY_OPEN
  → DELIVERY_CLOSING
  → DELIVERY_ROUND_POSTCLOSE_MEASURING
  → DELIVERY_WAIT_SELECTION
      ├─ CONTINUE → DELIVERY_HARD_SAFETY_CHECK → DELIVERY_OPENING
      └─ END/TIMEOUT → DELIVERY_FINALIZING → IDLE
```

1. 香橙派只有在 SQLite 已保存 `sessionUid`、整场价格/袋/配置快照、`continueDeliveryWaitMs`、`negativeWeightThresholdGram`、开始授权和本地截止点后，才发送 `START_DELIVERY_SESSION`。
2. MCU 先取得整场首次开门前稳定总重量并交给香橙派持久化；香橙派还要保存两张首次开门前照片。只有这些事实已可靠保存、开始授权仍有效且硬安全条件成立时，MCU 才执行首次开门。
3. 投递门继续使用真实门控和门磁，MCU 依次产生投递门 `OPENING/OPEN/CLOSING/CLOSED` 本地关键事件；用户完成本轮或本地超时都会关门，香橙派失联时 MCU 仍独立超时关投递门。
4. 每轮关闭后取得本轮稳定重量。为锁存负重量异常，设备本地比较本轮关门后与本轮开门前重量；减少量大于等于冻结阈值时只把 `negativeWeightAnomaly` 从 `false` 置为 `true`。本轮重量、减少值和门事件仅用于本地恢复，不进入 OneNet。
5. 用户在冻结选择窗口内选择继续时，MCU/香橙派只检查投递门真实可动作及门卡滞、执行器、称重过载、烟雾、本地存储等硬安全条件，然后直接在原 `sessionUid` 下再次开门。不查询普通满溢，不等待后端，不创建新作业 UID、云端命令或上行事件。
6. 用户选择结束或 30 秒窗口届满时，不再重新开门。MCU 提供最终关门后稳定重量和最终投递门真实状态；香橙派取得两张最终照片，把首次/最终重量、四个照片槽、冻结摘要、结束原因和锁存布尔值原子保存为唯一 `DELIVERY_COMPLETE`。
7. 中间按钮、开关门、重量和照片不得映射为 OneNet 事件。MCU 不决定订单、审核、满溢或钱包；订单始终按整场最终净重量形成一单。

### 3. 清运操作

1. `START_CLEAN_OPERATION` 到达前，香橙派已经可靠保存原 `operationUid`、旧袋/旧基准、新袋预留、配置摘要和本地期限。
2. MCU 先采集首次解锁前稳定总重量，发送 `WORK_PREUNLOCK_WEIGHT_READY`。香橙派将重量写入原操作 SQLite 后才 ACK；该 ACK 只是“重量已可靠保存”，不是门已打开。
3. 香橙派随后为首次动作创建并保存新的 `UNLOCK_CLEAN_DOOR`。MCU 登记该命令并可能执行电磁阀通电后，操作即越过不可取消边界；ACK 丢失时必须按“可能已通电”处理。清运门通电解锁后自动弹出，MCU 不具备物理门位检测。
4. 每次“重新开门”都是同一 `operationUid` 下的新 `UNLOCK_CLEAN_DOOR + mcuCommandUid`。它不重新扫描袋、不覆盖首次解锁前重量、不创建新云端操作。中途断网不妨碍尚在本地执行窗口内的原操作再次解锁。
5. MCU 只报告 `ENERGIZED/DEENERGIZED` 锁输出。锁通断只证明电磁阀输出，不能推定门扇
   `OPEN/CLOSED`；没有门磁时清运门物理状态保持 `UNKNOWN`，不得产生清运门
   `DOOR_OPENED/DOOR_CLOSED` 或门磁健康。
6. `CLEAN_FINISH_REQUESTED` 只是带原操作身份的按钮事实。只有清运员明确确认完成、锁输出
   为 `DEENERGIZED`，并取得最终稳定总重量或明确终态称重故障后，香橙派才可靠保存单一
   `CLEAN_COMPLETE`。人工确认必须作为独立事实保存，不能改写为传感器检测到门扇关闭；
   故障时重量为空并携明确状态/故障码。
7. 首次解锁可能执行后发生超时、香橙派重启或 MCU 重启时，设备先令电磁阀断电并保留原操作，进入 `RECOVERY_REQUIRED` 等待原清运员现场恢复；不得自动完成、结束操作或用 `SAFE_CLOSE` 处理清运门。只有后端取得“锁断电 + 原清运员现场确认门扇关闭”后，才可释放整机占位并继续锁住原投口/操作/袋预留。

### 4. 强类型 MCU 事件

P0 至少包含以下事件族：

| 事件 | 必备关联 | 语义 |
|---|---|---|
| `CONFIG_APPLY_RESULT` | `applicationUid + mcuCommandUid` | MCU 配置子集已应用或明确失败 |
| `WORK_PREOPEN_WEIGHT_READY` | 投递 `sessionUid`、投口、命令 | 整场首次开门前或本地轮次开门前稳定重量/失败；中间轮次不上传云端 |
| `DELIVERY_DOOR_STATE_CHANGED` | `sessionUid`、投口、命令 | 投递门真实门控阶段及门磁状态 |
| `WORK_POSTCLOSE_WEIGHT_READY` | `sessionUid`、投口、命令 | 本地轮次或最终关门后稳定重量/失败；只有最终值进入 `DELIVERY_COMPLETE` |
| `DELIVERY_SELECTION` | `sessionUid` | 本地 `CONTINUE/END/WINDOW_EXPIRED`；不映射为 OneNet 事件 |
| `WORK_PREUNLOCK_WEIGHT_READY` | `operationUid`、投口、命令 | 清运首次解锁前稳定总重量或失败 |
| `CLEAN_LOCK_POWER_CHANGED` | `operationUid + mcuCommandUid` | 电磁阀 `ENERGIZED/DEENERGIZED` 真实输出；不得派生门扇位置 |
| `CLEAN_UNLOCK_REQUESTED` | `operationUid` | 同一清运操作内请求新的再次解锁命令；名称描述电磁阀解锁，不宣称 MCU 控制门扇开合 |
| `CLEAN_FINISH_REQUESTED` | `operationUid` | 清运员请求完成原操作；不是完成事实 |
| `FULLNESS_SAMPLE_RESULT` | `detectionUid + sampleRole` | 红外、称重及两类来源健康的同次采样 |
| `BASELINE_MEASUREMENT_RESULT` | `measurementUid` | 空袋基准重测的稳定重量或失败 |
| `STATE_SNAPSHOT` | 当前 MCU boot | QUERY_STATE 的一致诊断快照 |
| `FAULT_OBSERVED` | 当前作业可空 | 投递门、清运锁、称重、红外、存储或 MCU 安全故障 |
| `SAFETY_SENSOR_EVENT` | 部署/投口，当前作业可空 | 烟雾等独立安全传感器报警、恢复或来源故障 |
| `SAFE_CLOSE_RESULT` | 安全命令 | 逐投递门真实关门结果；不含清运门 |

每条事件都包含 `mcuBootId + mcuEventSequence + uptimeMs`；作业事件还必须包含 `portNo + workType + workUid`，命令派生事件包含 `mcuCommandUid`。香橙派必须把中间投递 UART 事实限制在本地会话恢复数据中，只有整场最终结果获得独立 `eventUid + edgeEventSequence`。未知或已结束且不允许迟到的作业身份不得挂给当前用户或当前作业。

## I-049 重量、单价、门与传感器数据契约

**修订后确认：重量统一使用带符号整数克并明确稳定性，超时或故障不能伪装成 0；投递只以上传的整场首次/最终重量结算，负重量过程只上传锁存布尔值；清运锁状态与投递门物理状态分型。**

### 1. 投口与基础状态

- `portNo` 为 `uint8`，有效范围是 `1..HELLO.portCount`；0 只允许表示整机范围，不能代表第一个投口。
- 投递门状态闭集为 `CLOSED/OPENING/OPEN/CLOSING/JAMMED/UNKNOWN`，投递门健康闭集为 `OK/TIMEOUT/ACTUATOR_FAULT/SWITCH_FAULT/DISCONNECTED`；状态与健康分别上报，不能用 `CLOSED` 掩盖投递门门磁故障。
- 清运门不使用上述门状态/健康结构。MCU 只报告
  `cleanLockPowerState=ENERGIZED/DEENERGIZED` 和电磁阀健康；无独立门位传感器时
  `cleanDoorPhysicalState` 固定为 `UNKNOWN`。清运员确认关门另存
  `cleanerPhysicalCloseConfirmed=true` 与确认身份/时间，不能把锁通断或人工确认改写为
  门磁、`JAMMED` 或真实门位检测结果。
- 红外值闭集为 `CLEAR/BLOCKED/UNKNOWN`，健康闭集为 `OK/TIMEOUT/SENSOR_FAULT/DISCONNECTED`。
- 烟雾状态闭集为 `NORMAL/ALARM/UNKNOWN`，并独立携带 `OK/SENSOR_FAULT/DISCONNECTED` 健康。烟雾属于持续安全监测，不受“红外和重量只在满溢采样时读取”的限制；报警、恢复和来源故障均作为关键 `SAFETY_SENSOR_EVENT` 可靠交付。
- 称重结果状态闭集为：

  ```text
  STABLE
  UNSTABLE
  TIMEOUT
  SENSOR_FAULT
  OVERLOAD
  ```

- 只有 `STABLE` 的 `stableWeightGrams` 可以进入业务计算。其他状态可以携带最后观察值供诊断，但稳定重量字段必须为空/无效，香橙派不得用 0 或旧值兜底。
- 一个稳定负读数仍是可保存的带符号测量事实，不能仅因小于 0 自动改成传感器故障。是否超出已校准合理范围由 MCU 按配置返回明确故障。

### 2. MCU 采集结果

MCU 负责采样、滤波、消抖和稳定判断；每个测量结果至少返回：

```text
measurementStatus
stableWeightGrams（仅 STABLE 有效）
lastObservedWeightGrams（可选诊断）
measurementElapsedMs
sampleCount
calibrationVersion
sensorHealth
faultCode
```

稳定窗口、容许波动、连续样本数、量程和超时是设备型号/投口配置，不硬编码在香橙派业务流程。`APPLY_CONFIG` 必须带精确版本和摘要，MCU 返回同一版本/摘要后才形成配置 `APPLIED` 证明。

投递整场首次/最终重量、用于本地异常锁存的轮次重量，以及清运首次解锁前/最终确认重量都必须分别带自己的测量身份和 MCU 事件身份；不能读取一条“最近重量”冒充本次数据，也不能因两个数值相同就把后一个事件去重。投递轮次重量只留在边缘恢复上下文，不映射到 OneNet。

### 3. 投递与清运计算

投递由香橙派按整数克计算：

```text
deliveryNetWeightGrams = finalPostCloseTotalWeightGrams - firstPreOpenTotalWeightGrams
```

- 整场正、零、负结果都保留原值；整场负重量按既有规则等待人工审核，不在审核前进入用户钱包。
- 每个本地轮次另计算 `roundPostClose - roundPreOpen`。结果小于等于 `-negativeWeightThresholdGram` 时只锁存 `negativeWeightAnomaly=true`；阈值默认 500 克且来自开始时冻结配置。最终事件不上报轮次重量、具体减少值或触发次数，该标志也不改变整场净重量。
- 任一端重量不为 `STABLE` 时，不生成伪净重；作业携明确称重故障进入既有系统异常/恢复流程。

清运由香橙派结合其 SQLite 中冻结的旧基准计算：

```text
removedNetWeightGrams = preUnlockTotalWeightGrams - oldBaselineWeightGrams
newBaselineWeightGrams = cleanerConfirmedFinalTotalWeightGrams（仅稳定且关系合法时）
```

MCU 不保存旧袋业务基准，也不判断清运统计净重。再次解锁后此前候选最终重量作废，但不覆盖首次 `preUnlockTotalWeightGrams`；只有清运员确认、锁断电，以及最终稳定重量或明确终态称重故障同时成立时才形成完成候选。

### 4. 单价和屏幕金额

`unitPriceTenThousandths` 使用无符号整数：

```text
1 = 0.0001 元/千克
4500 = 0.4500 元/千克
```

它取自该投递会话开始时冻结配置；以后配置变化不影响当前会话。禁止使用 IEEE 754 浮点传输或临时协议中的一位价格数字。

权威返现金额仍由香橙派/后端按冻结规则重算并校验。若 MCU 屏幕显示估算金额，必须使用整数运算并与业务规则相同地先把原始克值换算为 0.01 kg 业务重量，再把金额四舍五入到分：

```text
businessWeightCentiKg = roundHalfUp(deliveryNetWeightGrams / 10)
estimatedFen = roundHalfUp(businessWeightCentiKg × unitPriceTenThousandths / 10000)
```

计算使用带符号 64 位中间值并检查溢出。`roundHalfUp` 对正负数都使用十进制定点的 HALF_UP 语义，绝对值恰为半个最小单位时向远离 0 的方向舍入；下一阶段用正、负和边界黄金样本固定跨语言结果。

屏幕值只是现场提示，不能由 MCU 上报后直接写钱包。

### 5. 满溢采样

1. MCU 不在空闲时间持续采集并上报业务满溢快照。只有整场投递结束并可靠保存唯一 `DELIVERY_COMPLETE` 后、清运完成后或工作人员发起真实重检时，香橙派才发送 `SAMPLE_FULLNESS`。投递中间每次关门和继续按钮都不触发普通满溢采样。
2. 命令携 `detectionUid`、`sampleRole=INITIAL/CONFIRMATION/MANUAL_RECHECK`、投口、配置摘要、等待时长和采集参数。`DELIVERY_COMPLETE/CLEAN_COMPLETE/MANUAL_RECHECK` 是检测触发类型，不混入样本角色；投递/清运后的首次样本均为 `INITIAL`。投递后首次等待默认 5 秒、疑似满溢确认复检间隔默认 10 秒，实际使用版本化配置。
3. 同一次结果分别返回红外值/健康和稳定总重量/健康；MCU 不把红外遮挡直接转换为满溢度，也不替香橙派组合业务模式。
4. 香橙派使用投递会话开始或清运操作开始时冻结的模式执行：

   - `INFRARED_ONLY`：可靠红外遮挡判满，来源失败则停止投递；
   - `WEIGHT_ONLY`：可靠重量结合有效基准和阈值判满，来源失败则停止投递；
   - `INFRARED_OR_WEIGHT`：任一可靠来源判满即可判满；一个来源不满而另一来源失败时不能判不满，必须停止投递；两个来源都可靠且不满才是不满。

5. 满溢度只按内容物净重量计算：

   ```text
   contentNetWeight = stableTotalWeight - currentBaselineWeight
   fullnessPercent = contentNetWeight / configuredFullWeight × 100%
   ```

   允许超过 100%；净重量为负时展示 0%，但原总重量、基准和负净重全部保留。基准缺失时重量满溢度为未知，不能显示为 0。
6. 满溢检测失败属于设备/来源故障，不属于用户投递异常，也不阻止已经完整形成的订单和返现审核。

## I-050 重启恢复、协议切换与验收

**修订后确认：`uart-v1` 重启后先 HELLO 和 QUERY_STATE，再用 SQLite 与 MCU 快照恢复；
`fixed-frame` 没有协议级查询，只能依靠 SQLite 证据并把未知物理状态安全锁存。清运首次
解锁可能执行后的重启必须等待原清运员；任何模式都不能自动重放旧开门。**

### 1. 分模式恢复顺序

`uart-v1` 下，香橙派进程启动或串口重连按以下顺序执行：

```text
校验 SQLite 与部署身份
  → 打开 UART
  → HELLO / HELLO_ACK
  → QUERY_STATE
  → 接收并持久 ACK MCU 待确认关键事件
  → 对比本地作业、mcuBootId、投递门、清运锁和 MCU 当前阶段
  → 必要时 SAFE_CLOSE 投递门 / 令清运锁断电
  → 收敛原作业或锁存恢复状态
  → 满足全部条件后才允许新作业
```

`fixed-frame` 下不发送 `HELLO/QUERY_STATE/SAFE_CLOSE`。启动时先校验 SQLite 与部署
身份、打开 UART、丢弃接收缓存中的旧结果，将未决投递明确失败，将可能已解锁的未决清运
转为 `RECOVERY_REQUIRED`，并把 MCU、门位和传感器状态保留为 `UNKNOWN/NOT_SAMPLED`；
现场人工收敛或明确解除安全锁前不允许新作业，也不重放任何旧 `AA/EE`。

任一模式的恢复过程完成前，香橙派不得发送新的
`START_DELIVERY_SESSION/START_CLEAN_OPERATION/UNLOCK_CLEAN_DOOR`。

### 2. QUERY_STATE 快照

`STATE_SNAPSHOT` 至少包含：

- 当前 `mcuBootId`、固件和协商协议版本；
- MCU 当前活动作业类型、`workUid`、投口、阶段和最近 `mcuCommandUid`，没有作业时显式为 `NONE`；
- 所有投递门真实状态/健康、清运电磁阀通断/健康、清运门位 `UNKNOWN` 与独立人工关门
  确认，以及称重/红外/烟雾状态与健康及安全故障；
- 已持久应用的配置版本、完整内容摘要与 MCU 子集摘要；
- 可空的配置 staging `applicationUid/version/digest/receivedParts`；
- 待确认关键事件数量、最早/最新 `(mcuBootId, mcuEventSequence)`；
- MCU 单调运行时间和看门狗/复位原因。

快照是恢复证据，不替代原关键事件；投递门当前关闭不能伪造丢失的投递完成，清运锁当前断电也不能伪造物理关门或清运员完成确认。

### 3. 重启与不一致矩阵

| 场景 | 处理 |
|---|---|
| 香橙派重启，MCU `bootId` 未变，本地投递会话与 MCU 作业一致 | 先补收/ACK MCU 本地事件，再按原 `sessionUid` 收敛；不得重新发送已经导致首次开门的旧开始命令 |
| 香橙派重启，投递门仍打开且双方作业一致 | 保留原会话，恢复事件接收并等待 MCU 独立超时关门；必要时发送新的幂等 `SAFE_CLOSE`，不得自动再次开门 |
| 香橙派重启，投递门已关闭且 MCU 有最终待确认结果 | 原事件提交 SQLite 后 ACK，沿原 `sessionUid` 生成唯一整场完成事实；中间轮次事件只恢复本地锁存状态 |
| 清运首次解锁可能执行后任一端重启 | `uart-v1` 先令电磁阀断电；`fixed-frame` 无恢复控制帧，不能宣称已断电。两者都保留原 `operationUid`、占位和预留，门位保持 `UNKNOWN` 并进入 `RECOVERY_REQUIRED`，等待原清运员现场恢复，不自动生成 `CLEAN_COMPLETE` |
| MCU `bootId` 改变 | 视为 MCU 易失状态已丢失；先逐投递门证明安全关闭并令清运锁断电，原投递进入结果待核查、原清运进入原清运员恢复流程，禁止重放旧 START/UNLOCK |
| SQLite 与 MCU 当前作业不一致 | 整机安全锁存；先安全关门、保存两边证据并人工核查，不以“较新时间”覆盖任一方 |
| 投递门状态 `UNKNOWN/JAMMED`、清运锁状态未知/故障或 MCU 不可达 | 停止整机新作业；投递门继续安全关门/诊断，清运锁断电并等待人工，不能只释放后端作业恢复使用 |
| 配置版本/摘要不同 | 部署保持不可激活或停止新作业，重新执行原配置应用；不得使用旧一位单价帧兜底 |
| MCU 待确认事件队列与声明范围矛盾 | 锁存协议/存储故障，保留 SQLite 与 MCU 证据，不跳过缺号继续开放作业 |

`uart-v1` MCU 自身启动时执行安全初始化：禁止业务开门，尝试关闭非关闭的投递门并保留
真实结果，同时令清运电磁阀断电但不报告清运门已物理关闭；完成 HELLO 和状态核对前只
接受握手、查询、投递门 `SAFE_CLOSE` 和恢复所需的锁断电控制。`fixed-frame` 线路无法
查询或命令这些恢复状态，香橙派不得把该规范行为假定为已发生。

### 4. 正式切换

所选 MCU 协议模式和香橙派版本必须作为一个兼容组合发布：

1. 禁用旧 D1 文本解析/发送及旧清运 gross/tare 路径；
2. 配置且只启用 `uart-v1` 或 `fixed-frame` 一个解析器；前者使用本章 Registry，
   后者只使用
   [`ecobin-mcu-fixed-frame-v2` 完整单文件协议](../../../hardware/docs/单片机-香橙派适配通信协议详细内容.md)
   并显式拒绝缺失能力；
3. 更新 OneNet 物模型、后端事件/命令适配和香橙派/MCU 模式兼容矩阵；
4. `uart-v1` 对每台真机完成 HELLO、配置摘要、状态、称重、门控和重启验收；
   `fixed-frame` 按 H-03 完成全部 11 类帧、活动作业绑定、屏幕流程、称重、电磁阀和重启验收；
5. 只有所选模式的协议/适配、全部可观察门锁安全事实和未收敛作业均通过对应门槛时，
   部署才可激活；固定帧缺失状态不能用成功占位。

生产不得同时运行“先尝试新协议、失败后解析旧帧”的双协议逻辑。离线测试工具可以读取旧抓包用于迁移审计，但不能进入生产串口读写路径。

## 机器产物与契约验证目标

以下是 UART 共同基线的完整交付目标。按后确认的 I-055，首版形成前允许随 MCU/香橙派进度分批实现，不要求在开始编写任一端代码前先完成全部黄金样本、契约 CI 或自动 HIL 发布门禁；某条真实设备切片启用前，仍必须人工验证与其相关的帧、门控、称重、重试和重启安全，正式切换条件不变。

1. 单一机器可读 UART 注册表：帧版本、message type 数值、flags、每种 payload 字段/偏移/范围、能力位、ACK/NACK 和故障码。
2. 从注册表生成或校验的 MCU C 头文件/编解码器与 Python 3.11 模型；两端不得各自手写另一套枚举。
3. CRC 和帧黄金样本：空载荷、普通载荷、payload 内含 `EC 42`、最大 256 字节、负重量、最高合法枚举和错误帧。
4. 解析测试：逐字节拆分、任意粘包、前后噪声、伪 magic、非法长度、CRC 错、未知 flags/type、帧超时和 512 字节缓冲上限。
5. 可靠传输测试：ACK 丢失、三次重发、重复同命令、同 ID 异摘要、双方同时发送、MCU 事件 ACK 前后强杀香橙派。
6. 状态机测试：投递整场首次/最终重量、多轮本地继续、30 秒结束、过程不上云、负重量
   布尔锁存；清运首次解锁可能执行、锁断电但门位仍为 `UNKNOWN`、多次再次解锁、缺少
   清运员确认不得完成和首次解锁前结束。
7. 传感器测试：正/零/负稳定重量、UNSTABLE/TIMEOUT/FAULT/OVERLOAD、红外失败、两来源组合、烟雾报警/恢复/来源故障、单价 4 位精度及金额整数舍入。
8. 重启矩阵：香橙派在每个物理阶段重启、MCU 在每个阶段复位、双方同时断电、ACK 前后断电、配置不同步和门状态未知。
9. 真机 HIL 验收：投递门动作和清运锁通电期间制造串口噪声/丢 ACK，验证不重复驱动；断网、断电后使用原业务身份收敛，证明 MCU 独立超时关闭投递门，并证明清运流程不会把锁断电误报为物理关门或自动完成。
10. 模式隔离负向测试：`uart-v1` 收到固定帧、`fixed-frame` 收到 UART 1.0/D1/文本时
    只计协议噪声/故障；任一模式都不能自动切换解析器或触发错误动作。

## 本章明确不设计

- MCU 电路、GPIO、电机驱动电流、称重 ADC 型号或具体滤波算法；
- UART 总线加密、带密钥消息认证和机箱防拆；CRC/SHA-256 不提供这些能力；
- 远程 MCU 固件升级、A/B 分区、固件签名和回滚流程；
- 通用远程控制台、任意寄存器读写或生产调试 shell；
- MCU 保存租户、机构、用户、钱包、订单或微信状态；
- 使用 UART ACK 代替门状态、稳定重量、OneNet 上行或后端业务确认；
- 使用浮点价格、无符号重量、0 值超时、最近重量或无身份按钮事件；
- MCU 重启后自动重放旧开门，或香橙派用当前门/重量猜造丢失作业；
- 正式生产中的旧 D1、自动探测、`fixed-frame` 与 `uart-v1` 同时解析或失败回退。

## 现状审计参考

- [UART 协议审计报告](../../../hardware/docs/review/uart-protocol-audit.md)
- [UART 协议临时兼容约束](../../../hardware/docs/review/uart-protocol-temporary-compatibility.md)
