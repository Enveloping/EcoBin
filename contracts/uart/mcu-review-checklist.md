# EcoBin UART 1.0 MCU 联合确认单

本确认单是 F-10 的人工 checkpoint。机器来源是
[`uart-registry.yaml`](uart-registry.yaml)，展开后的偏移、长度和摘要见
[`generated/uart-layout.json`](generated/uart-layout.json)。本文件只记录评审结论，不在
这里另建第二套消息号或字段定义。

## 1. 当前状态

| 项目 | 当前值 |
|---|---|
| 候选版本 | `1.0.0-rc.2` |
| 线协议 | major `1` / minor `0` |
| 物理串口 | `115200 / 8N1 / no flow control` |
| Registry 状态 | `MCU_REVIEW_REQUIRED` |
| 软件生成/校验 | 已完成 |
| MCU 逐字段确认 | 待完成 |
| C 固件工具链黄金样本 | 待完成 |
| 真机/HIL | 属于 H-03，尚未开始 |

在本单完成前，F-10 不能标记 `done`，H-03 不能把候选编号当成已经共同冻结的固件契约。

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

- [ ] 37 个 message type 的编号、方向和 ACK 规则无冲突。
- [ ] 每条固定 payload 的字段顺序、偏移、宽度、符号和范围可由 MCU 实现。
- [ ] UUID 为 16 字节网络顺序，SHA-256 为 32 字节原值，重量为带符号 `int32` 克。
- [ ] UUID 默认禁止全零；仅 Registry 明示的可空作业、最新命令和 staging sentinel
  按条件使用全零编码。
- [ ] 单价为“元/千克 × 10000”的 `uint32`，所有时长为整数毫秒。
- [ ] capability bit 0～12 与目标板能力一致，没有清运门门磁、清运门自动关门或清运
  `SAFE_CLOSE` 能力。
- [ ] 配置分段固定为 `BEGIN(1) → DEVICE(2) → PORT(3..N+2) → COMMIT(N+3)`，
  `partCount=N+3`，N 为投口数。
- [ ] 状态快照固定为 `BEGIN(1) → PORT(2..N+1) → END(N+2)`，
  `partCount=N+2`，整份摘要通过后才可应用。
- [ ] `PortFaultBitmap` 只使用 bit 0～4，bit 5～31 必须为 0，且每一位与对应 health
  字段是否为 `OK` 严格一致。
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
- [ ] `CLEAN_FINAL_WEIGHT_READY` 用于完成请求后的最终稳定重量或明确终态称重失败；
  MCU 负责人确认该独立消息与屏幕状态机匹配。
- [ ] `SAFE_CLOSE` 只控制投递门；清运恢复最多令电磁阀断电并等待原清运员现场确认。

### 3.4 非易失与重启

- [ ] `mcuBootId` 每次真实重启改变且非零，首选持久单调启动计数器。
- [ ] 关键事件在香橙派 SQLite 持久 ACK 前保存原
  `(mcuBootId, mcuEventSequence, payload)`。
- [ ] 可能导致开门/解锁的命令 UID、摘要和既有处置可跨看门狗/掉电去重。
- [ ] 当前作业、安全门阶段、清运锁可能通电边界和配置摘要可在重启后查询。
- [ ] 配置 staging 与 COMMIT 原子切换，半份配置重启后不会成为 `APPLIED`。
- [ ] MCU/香橙派重启后先 HELLO、QUERY_STATE 和补交关键事件，绝不自动重放旧开门。
- [ ] 负责人说明非易失介质、可用容量、擦写寿命、队列上限及满队列安全行为。

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
C UART golden vectors: 10 frames, 10 stream traces, 3 digests passed
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
