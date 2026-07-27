# EcoBin UART 1.0 MCU 联合确认单

本确认单用于选择 `uart-v1` 原生 MCU 实现时的可选符合性检查。机器来源是
[`uart-registry.yaml`](uart-registry.yaml)，展开后的偏移、长度和摘要见
[`generated/uart-layout.json`](generated/uart-layout.json)。本文件只记录评审结论，不在
这里另建第二套消息号或字段定义。

## 1. 当前状态

| 项目 | 当前值 |
|---|---|
| 候选版本 | `1.0.0-rc.3` |
| 线协议 | major `1` / minor `0` |
| 物理串口 | `115200 / 8N1 / no flow control` |
| Registry 状态 | `FROZEN_REFERENCE` |
| 软件生成/校验 | 已完成 |
| MCU 逐字段确认 | 已完成 |
| C 固件工具链黄金样本 | 原生 `uart-v1` 部署时验证 |
| 真机/HIL | 原生 `uart-v1` 部署时验证；当前固定帧 HIL 归 H-03 |

F-10 已完成，现有固定帧单片机无需执行本确认单。下列复选框保留为完整 `uart-v1`
固件符合性清单；若以后部署该模式，消息布局变更后必须重新确认。旧脉冲布局或 rc.2
时期的 `0x300` 挥发 HIL 不代表现行 rc.3 新布局已经验证，也不能替代对应部署验收。

## 2. 评审输入

MCU 负责人应取得同一份仓库状态中的：

- `contracts/uart/uart-registry.yaml`；
- `contracts/uart/generated/uart-layout.json`；
- `contracts/uart/generated/c/ecobin_uart_protocol.h`；
- `contracts/uart/generated/c/ecobin_uart_golden_test.c`；
- `contracts/examples/uart/golden-vectors.json`；
- `contracts/examples/uart/stream-traces.json`；
- `contracts/examples/uart/digest-vectors.json`；
- `contracts/generated/contract-catalog.md`；
- `docs/planning/interface-design/10-uart-protocol-i046-i050.md`。

生成文件头中的 Registry SHA-256 必须与 `golden-vectors.json` 相同。摘要不一致时停止评审，
重新运行生成器，不能手工改 C 头文件。

## 3. 必须逐项确认

### 3.1 帧与传输

- [ ] 串口固定为 `115200 baud、8 data bits、no parity、1 stop bit、no flow control`；
  设备路径由香橙派部署配置，不写进业务消息。
- [ ] magic 为 `EC 42`，多字节整数为 big-endian。
- [ ] 完整帧最长 256 字节，payload 最长 242 字节，接收缓冲硬上限 512 字节。
- [ ] CRC 为 CRC-16/CCITT-FALSE，`123456789 → 29B1`。
- [ ] 候选帧接收期限 100 ms；ACK 期限 500 ms；同一完整帧最多发送 3 次。
- [ ] CRC 通过前不相信版本、flags、消息号、序号或 payload。
- [ ] ACK 只表示可靠受理，不表示门、锁、称重或业务完成。

### 3.2 消息、字段与能力

- [ ] 39 个 message type 的编号、方向和 ACK 规则无冲突。
- [ ] 每条固定 payload 的字段顺序、偏移、宽度、符号和范围可由 MCU 实现。
- [ ] UUID 为 16 字节网络顺序，SHA-256 为 32 字节原值，重量为带符号 `int32` 克。
- [ ] UUID 默认禁止全零；仅 Registry 明示的可空作业、最新命令和 staging sentinel
  按条件使用全零编码。
- [ ] 单价为“元/千克 × 10000”的 `uint32`，所有时长为整数毫秒。
- [ ] capability bit 0～14 与目标板能力一致；握手基线是 `0x300`，完整已知掩码是
  `0x7fff`。未实现时不得宣称持久去重/事件或 `DELIVERY_DOOR_HIL_QUALIFIED`，也没有
  清运门门磁、清运门自动关门或清运 `SAFE_CLOSE` 能力。
- [ ] 配置分段固定为 `BEGIN(1) → DEVICE(2) → PORT(3..N+2) → COMMIT(N+3)`，
  `partCount=N+3`，N 为投口数。
- [ ] 状态快照固定为 `BEGIN(1) → PORT(2..N+1) → END(N+2)`，
  `partCount=N+2`，整份摘要通过后才可应用。
- [ ] `PortFaultBitmap` 只登记 bit 0、1、2、4，其他位必须为 0；bit 0 来自最近门命令
  输出失败，bit 1/2/4 来自对应 health。超声波无回波/样本不足不形成故障位。
- [ ] 快照 applied 配置使用“version=0 + 两个全零摘要”或“version>0 + 两个非零摘要”；
  staging 有效时 `stagingPartCount=portCount+3`、bit 0 已置位且高于 partCount 的位为 0。

### 3.3 投递与清运状态机

- [ ] 一个 `sessionUid` 可在 MCU 本地执行多个继续轮次，但只形成整场首重、末重和一次
  OneNet `DELIVERY_COMPLETE`。
- [ ] 中间按钮、门事件、重量和减少值只留在边缘恢复上下文，不上 OneNet。
- [ ] 任一轮次减少达到冻结阈值时只锁存布尔 `negativeWeightAnomaly`。
- [ ] `CLEAN_LOCK_POWER_CHANGED` 只表达电磁阀 `ENERGIZED/DEENERGIZED`，不能生成
  `DOOR_OPENED/DOOR_CLOSED`。
- [ ] `CLEAN_FINISH_REQUESTED` 只是清运员按钮/确认请求，不是完成事实。
- [ ] `CLEAN_FINAL_WEIGHT_READY` 用于完成请求后的最终称重结果（含 `UNSTABLE` 兜底值）
  或明确终态称重失败；
  MCU 负责人确认该独立消息与屏幕状态机匹配。
- [ ] `UNSTABLE` 必须携带最后四次或全部可用样本均值，允许投递；其他故障只要仍有
  数据也必须携带，数值存在性和可信度不得混为一谈。
- [ ] 投递门 OPEN 固定为 `PB6=0、PB7=1`，CLOSE 固定为 `PB6=1、PB7=0`；
  换向先进入 `PB6=0、PB7=0` 100 ms 死区，再持续保持新方向直到相反命令或复位。
- [ ] `DELIVERY_DOOR_COMMAND_RESULT` 和 `SAFE_CLOSE_RESULT` 只报告方向与输出结果，
  不携带脉冲时长；物理门位始终 `NOT_OBSERVABLE`。
- [ ] 已受理但尚未实际下发的旧方向命令被新方向取代时报告
  `COMMAND_SUPERSEDED_BEFORE_DISPATCH`，不得再使用
  `PARTIAL_OUTPUT_INTERRUPTED`。
- [ ] `SAFE_CLOSE` 只控制投递门；清运恢复最多令电磁阀断电并等待原清运员现场确认。

### 3.4 首版挥发状态与重启

- [ ] `mcuBootId` 在每次真实重启后改变且非零；首版不要求持久单调。
- [ ] 当前上电周期内，关键事件在香橙派 SQLite ACK 前保存原
  `(mcuBootId, mcuEventSequence, payload)`；掉电后允许丢失。
- [ ] 配置 staging 与 COMMIT 在 RAM 中原子切换，半份配置不会成为 `APPLIED`；重启后
  配置回到 `EMPTY` 并由香橙派重新下发。
- [ ] MCU 重启先复位 PB6/PB7/PB8 并进入 `BOOT_RECOVERY`；投递中断不恢复。
- [ ] 香橙派在 HELLO、QUERY_STATE 和配置同步后显式发送
  `CONFIRM_NO_ACTIVE_WORK` 或带 `nextCleanActionSequence` 的
  `RESUME_CLEAN_OPERATION`；恢复不得重启清运窗口。
- [ ] 持久命令去重、持久事件队列、持久单调 boot ID、`targetMcuBootId` 和跨重启
  exactly-once 均列为后续升级，当前能力位不得误报。

## 4. C 黄金样本

在实际 MCU 编译器或与其 ABI/整数模型等价的 C11 工具链中编译：

```text
<cc> -std=c11 -Wall -Wextra -Werror \
  -Icontracts/uart/generated/c \
  contracts/uart/generated/c/ecobin_uart_golden_test.c \
  -o ecobin_uart_golden_test
```

预期输出：

```text
C UART golden vectors: 11 frames, 10 stream traces, 3 digests passed
```

还需记录编译器名称/版本、目标架构、命令、输出和 Registry SHA-256。仅在普通 PC
编译通过不能替代目标固件工具链确认。

## 5. 评审结论

| 项目 | 填写 |
|---|---|
| MCU 负责人 |  |
| 日期/时区 |  |
| MCU/板卡型号 |  |
| 固件工具链与版本 |  |
| 可实现的 capability bitmap |  |
| 非易失介质与保留能力 |  |
| C 黄金样本结果/证据位置 |  |
| 需修改的消息/字段 |  |
| 结论 | `APPROVED` / `CHANGES_REQUIRED` |

若为 `CHANGES_REQUIRED`，先修改唯一 Registry、重新生成全部制品和黄金样本，再重新评审；
不得在 MCU 代码中私自采用另一组编号或字段偏移。若为 `APPROVED`，主审复核证据后再把
F-10 integration/acceptance 推进，并解除 H-03/F-11 的相应契约阻塞。

## 6. 2026-07-25 rc.2 局部真机证据

- 真实 HELLO：`stm32f103rct6` / `1.0.0-hil.3` / capability `0x300` / 一投口。
- 当时的配置 BEGIN/DEVICE/PORT/COMMIT、独立 APPLY_RESULT、重复 COMMIT 去重和完整
  QUERY_STATE 摘要校验通过；rc.3 已改变配置字段、摘要和快照布局，必须重新执行。
- MCU boot ID 已限制在 `1..9007199254740991`；空关键事件队列的 oldest/latest
  四个范围字段均严格为零。
- 详细命令、结果和未关闭边界曾在独立 F-11 worktree 留存；该历史切片不代表当前
  固定帧线路或完整 `uart-v1` 部署验收。
- MCU 实际 ARMCC/Keil 尚未单独执行生成的 C 黄金程序；HELLO 的 RCT6 identity 与
  Keil target 名称中的 C8 也待核对。因此本节只保存局部历史证据，不关闭当前固定帧
  H-03，也不证明完整 `uart-v1` 部署符合性。

## 7. 2026-07-26 持续锁存门控破坏性变更

- `CONFIG_DEVICE_BLOCK` 已删除 `deliveryDoorOpenCommandSignalMs` 和
  `deliveryDoorCloseCommandSignalMs`，固定 payload 长度为 163 字节。
- `DELIVERY_DOOR_COMMAND_RESULT`、`SAFE_CLOSE_RESULT` 已删除 `actualOutputMs`，
  固定 payload 长度分别为 60 和 43 字节；`STATE_SNAPSHOT_PORT` 已删除
  `lastDeliveryDoorActualOutputMs`，固定 payload 长度为 81 字节。
- `mcuPayloadSha256` 摘要输入、UART 黄金向量、OneNet 配置/事件投影和三端生成物已随
  上述布局重建。旧香橙派与新 MCU（或新香橙派与旧 MCU）不得混用，否则配置长度、
  摘要或门结果解码会不一致。
