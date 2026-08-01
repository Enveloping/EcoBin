# 03｜可靠任务、OneNet/COS、边缘 SQLite 与 UART

> 上级索引：[EcoBin P0 详细设计与任务拆分](../detailed-design-draft.md)
>
> 状态：**已批准；F-08、F-10、F-11 已完成；H-03 真机验收已 ready 但尚未授权**
>
> 审查日期：2026-07-24
>
> 适用基线：A-001～A-020、D-014～D-015、D-026～D-030、D-036～D-045、I-041～I-050、I-052～I-055

## 1. 本章裁决

| 编号 | 裁决 |
|---|---|
| DD-006 | OneNet/Pulsar 入站固定为“可信规范化 → inbox/处理任务事务 → 传输 ACK → worker 领域事务”；消费者不得同步执行完整业务。 |
| DD-007 | 中心可靠任务使用数据库租约、稳定 `taskKey`、独立 attempt 和 `wakeVersion`；设备/OneNet与资金/微信至少使用两个隔离执行通道。 |
| DD-008 | 香橙派保持 Python 3.11 单进程，但以 SQLite 作为唯一作业真相；MQTT、UART、相机和 COS 调用不得持有 SQLite 事务。 |
| DD-009 | I-050 的六投口一致状态快照采用 `STATE_SNAPSHOT_BEGIN + PORT[] + END` 应用级分段；它不是通用 UART 分片。 |
| DD-010 | 正式边缘运行只允许显式选择一种 MCU 协议模式。当前现有单片机使用已确定的固定帧适配，规范 `uart-v1` 保留为可选实现；禁止自动探测、失败回退或同时双解析。旧 D1、QoS 0、内存作业和 `/tmp` 照片降级路径必须退出。 |

## 2. 当前实现必须整体退出的部分

当前设备链仍存在以下目标阻断：

- 香橙派作业、清运记录标识和重量状态主要在内存；
- MQTT 使用 `clean_session=True`、QoS 0；
- OneNet 服务回调同步执行物理流程；
- 上行仍是 `deliveryComplete/cleanGross/cleanTare`，以平台消息 ID 辅助幂等；
- 后端北向消费在业务异常后仍可能 ACK；
- OneNet 下行配置缺失时记录占位成功，调用失败只写日志；
- 下行日志可能输出含 COS 临时凭证的完整 `input`；
- Pulsar TLS 允许不安全连接；
- 旧投递 AA/BB/CC/DD 和清运 D1 没有统一业务映射；当前固定帧路线只允许经 F-11
  显式适配器使用已确定的 AA/BB/EE/DD/EF 帧，并对缺失 CRC、ACK、命令身份和状态查询
  明确限制，不能继续调用旧业务链；
- 设备独占只存在于投递进程锁，清运不共享；
- 照片位于 `/tmp`，文件名可覆盖，也没有作业前缀、补传和业务确认；
- 重启后没有 SQLite/MCU 双事实对照，串口失败仍可继续“纯 MQTT 模式”。

这些路径只能用于理解旧行为。正式实现不得在其上继续扩展兼容分支。

## 3. 中心侧可靠收件

### 3.1 入站适配器

`ecobin-integration` 的 OneNet、微信入站适配器只执行：

1. 验证传输来源、TLS、签名/加密和允许的产品/商户；
2. 解析协议信封，获得稳定外部身份；
3. 根据机器 Schema 规范化字段、单位、枚举和规范摘要；
4. 调用 operations 的可信收件端口；
5. 收件事务提交后才向 OneNet/Pulsar/微信返回传输成功。

协议原文、SDK 类型和临时凭证不得进入业务模块。日志只保存：

```text
correlationId
sourceKind
trustedSourceId
messageType
stableExternalId
canonicalSha256
schemaVersion
outcome/errorClass
```

不得保存完整解密正文、微信 code/OpenID、COS Secret、OneNet access key 或照片授权。

### 3.2 收件事务

operations 公开：

```text
acceptTrustedMessage(TrustedInboundEnvelope)
```

事务内完成：

```text
锁定/插入唯一 inbox
  → 校验 stableExternalId + canonicalSha256
  → 首次消息创建 PROCESS_INBOX:<inboxUid> 任务
  → 重复同摘要唤醒原任务
  → 同 ID 异摘要写 quarantine/冲突
  → 提交
```

返回值只有：

```text
ACCEPTED
DUPLICATE_ACCEPTED
QUARANTINED
```

数据库暂时失败、身份不可验证或无法解析稳定 ID 时不返回伪成功。OneNet/Pulsar 允许重投；无法认证的原文只按安全日志策略记录，不进入可信 inbox。

### 3.3 业务处理事务

worker 领取 `PROCESS_INBOX` 后先提交租约事务，再调用对应权威业务用例：

```text
读取 inbox 的不可变定位信息，不持有任务租约锁
  → 按 D-037/D-038 从领域永久锁根重新进入
  → 锁定公共事件头并重验作用域/摘要/目标
  → 幂等创建或推进领域事实与派生可靠任务/边缘确认
  → 最后条件更新 inbox、当前 attempt 和 task
  → 同一事务提交
```

业务失败整体回滚。暂时错误保留原任务重试；永久 Schema、来源身份或同 ID 异摘要冲突才进入隔离。不能为了停止设备重投而提前生成 `BUSINESS_APPLIED`。

领取事务只操作 `ops_reliable_task/attempt` 并立即提交；领域处理事务不得先锁 inbox/task 再反向申请设备、容量、钱包或机构账户锁。各纵向章节的任何简图若省略 operations 尾部，都以 D-039 的“领域根在前、operations 最后”作为唯一规范。

## 4. 中心可靠任务执行器

### 4.1 任务模型

`ops_reliable_task` 至少形成以下概念字段：

```text
taskUid
taskKey
taskType
targetType + targetUid
payloadSchemaVersion
payloadCanonicalSha256
state
nextAttemptAt
attemptCount
leaseOwner
leaseUntil
wakeVersion
blockedReason
createdAt/updatedAt/completedAt
```

约束：

- `taskKey` 全局唯一，表达同一业务意图；
- 相同 key、相同摘要复用原任务；
- 相同 key、不同摘要进入冲突，不覆盖 payload；
- `wakeVersion` 单调增长，使领域新事实能够唤醒等待中的原任务；
- `RUNNING` 不是永久所有权，租约过期后可被其他 worker 接管；
- `DONE` 只表示该任务的业务完成条件成立，不表示一次 HTTP/MQ 调用返回成功。

### 4.2 领取

同类 worker 每批执行：

1. 使用数据库时间选择 `PENDING/RETRY_WAIT` 且 `nextAttemptAt <= now`，或租约过期的 `RUNNING`；
2. 以 `FOR UPDATE SKIP LOCKED` 或等价 MySQL 8.4 原子方式领取有界批次；
3. 写 `leaseOwner/leaseUntil` 并递增 attempt；
4. 提交领取事务；
5. 事务外执行领域复核或外部调用；
6. 新短事务追加 attempt 结果并推进/退避。

禁止在持有 task 行锁时调用 OneNet、COS 或微信。

### 4.3 执行通道

同一 Spring Boot 单实例至少隔离：

| 通道 | 任务示例 | 隔离原因 |
|---|---|---|
| `iot-device` | inbox、设备下行、边缘确认、照片授权 | 设备事件洪峰不能占满资金线程 |
| `funds-wechat` | 支付/转账创建、查单、撤销、资金入账 | 资金超时、限频和重试必须独立 |
| `maintenance` | 长时间未结算、对账、告警聚合、清理 | 低优先工作不能阻塞主链 |

每个通道独立配置：

```text
workerCount
batchSize
leaseDuration
externalTimeout
maximumInFlight
boundedQueueCapacity
backoffPolicy
```

具体数值通过联调确定，不能使用无界内存队列。应用优雅停机时先停止领取新任务/新 MQ 消息，再等待当前短事务和有界外部调用；未完成工作由租约恢复。

### 4.4 外部调用边界

可能产生不可逆外部效果的任务使用：

```text
业务事务：固定外部业务 ID 和不可变请求快照
  → attempt 短事务：记录 externalCallMayHaveStartedAt
  → 事务外调用
  → 观察短事务：追加响应/错误
  → 领域归并事务：按当前事实收敛
```

进程在“可能已调用”之后崩溃时，只能以原身份查单或幂等续办，不能创建新 `commandUid`、`outBillNo` 或业务作业。

## 5. 香橙派运行组件

保持一个 Python 3.11 进程，组件按责任拆分：

| 组件 | 唯一职责 |
|---|---|
| `EdgeStore` | SQLite Schema、短事务和唯一写入口 |
| `MqttInboundAdapter` | 校验命令、持久受理并快速返回，不执行物理动作 |
| `WorkCoordinator` | 唯一推进投递/清运/检测/测量及生成 MCU 命令 |
| `UartLinkManager` | HELLO、严格解析、停等发送、事件 ACK、QUERY_STATE |
| `EventPublisher` | 扫描 `event_outbox`，QoS 1 上报并记录传输观察 |
| `PhotoWorker` | 拍照登记、COS 上传、补授权、永久缺失 |
| `ConfirmationHandler` | 持久化后端确认并建立确认回执 |
| `RecoverySupervisor` | 启动恢复、期限、磁盘、门安全和不一致锁存 |

组件之间通过有界内存通知唤醒，但 SQLite 才是恢复真相；队列丢失不能导致业务事实丢失。

## 6. SQLite 设计

### 6.1 文件与连接

目标 Linux 设备使用受控持久目录，例如：

```text
/var/lib/ecobin/edge/edge.db
/var/lib/ecobin/edge/photos/
```

实际路径由部署配置决定，不硬编码到业务协议。数据库参数：

```text
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=FULL;
```

规则：

- 一个 `EdgeStore` 负责所有写连接；
- 进程内锁串行化写事务；
- 所有写事务显式 `BEGIN IMMEDIATE`，保持短小；
- MQTT、UART、相机、文件上传和等待门动作不在事务内；
- 启动校验 Schema 版本、部署身份和 `PRAGMA quick_check`；
- 上次非正常退出或存储异常时执行完整 `integrity_check`；
- 校验失败、数据库丢失或部署身份不一致时进入 `SAFETY_LOCKED`，不能创建空库后沿同一部署身份重置事件序号。

### 6.2 表族

| 表 | 核心字段/责任 |
|---|---|
| `edge_meta` | Schema、部署码、硬件 SN、全局事件末序号、干净停机和存储健康 |
| `edge_config_application` | 完整配置、版本、完整摘要、MCU 子集摘要、应用阶段 |
| `edge_work_slot` | 单行整机作业权；投递与清运共用 |
| `command_inbox` | 云端命令、摘要、目标、期限、受理/拒绝/终态 |
| `delivery_session` | 云端唯一投递作业：冻结价格/配置/袋/阈值，保存首末重量、最终异常标志和完成状态 |
| `delivery_local_diagnostic` | 可选、有界轮转的本地继续轮次诊断；不上 OneNet、不参与订单或恢复身份 |
| `clean_operation` | 旧袋/基准、新袋、首次重量、解锁尝试/恢复代际、人工关门确认和最终结果 |
| `detection_context` | 检测身份、角色、规则快照和来源结果 |
| `measurement_context` | 基准重测目标、代际和结果 |
| `mcu_command` | MCU 命令身份、摘要、编码载荷、状态与重放策略 |
| `mcu_event_inbox` | MCU boot/sequence、强类型事件、摘要和 ACK 状态 |
| `event_outbox` | event UID、全局序号、首次生成的规范 JSON、业务确认状态 |
| `photo_outbox` | 作业/槽位、照片身份、路径、摘要、上传/关联状态 |
| `confirmation_inbox` | 后端确认、原事件摘要和回执状态 |
| `idempotency_tombstone` | 已清理命令/事件/确认/照片的 ID、摘要和原终态 |
| `edge_fault` | 存储、协议、门、传感器、恢复不一致故障 |

最低约束：

- `edge_work_slot` 最多一条活动作业；
- session UID、所有作业 UID、命令 UID 和事件 UID 唯一；云端不分配投递轮次序号；
- `(mcu_boot_id, mcu_event_sequence)` 唯一；
- `edge_event_sequence > 0` 且同一部署唯一；
- `(work_type, work_uid, slot)` 照片唯一；
- 同一稳定 ID 不同摘要不得覆盖。

### 6.3 五个不可拆本地事务

1. **收命令**：命令正文/摘要、业务上下文、本地期限和受理观察共同提交，之后才返回 `ACCEPTED`。
2. **收 MCU 事件**：`mcu_event_inbox` 提交后才发送 UART ACK。
3. **建边缘事件**：分配全局序号、生成规范正文、写 outbox 在同一事务。
4. **收业务确认**：核对原摘要、标记已确认、保存确认并建立回执在同一事务。
5. **登记照片**：完整写临时文件、`fsync(file)`、同文件系统原子改名、同步父目录后，才把正式文件登记为 `LOCAL_READY`。

### 6.4 本地状态

```text
command:
RECEIVED → ACCEPTED → EXECUTING → SUCCEEDED/FAILED
    └────→ REJECTED（终态，不得进入 EXECUTING）

event:
PENDING → MQTT_PUBACKED → BUSINESS_CONFIRMED
                        ↘ EVENT_QUARANTINED

photo:
CAPTURE_PENDING → LOCAL_READY → UPLOAD_PENDING → AVAILABLE → ASSOCIATED
                                ↘ PERMANENTLY_MISSING

coordinator:
BOOT_RECOVERY → READY → DELIVERY_ACTIVE/CLEAN_ACTIVE
             ↘ SAFETY_LOCKED
```

MQTT PUBACK 只证明传输层接受，不能删除事件。只有稳定业务确认让事件、照片和必要作业上下文进入清理资格。

## 7. OneNet 命令与事件

### 7.1 MQTT

- 使用稳定 client ID、持久会话和 QoS 1；
- 网络恢复后扫描 SQLite，而不是依赖 Paho 内存重发；
- 命令回调只完成 Schema、部署、身份、摘要、期限校验和持久受理；
- 即时响应只包含：

```text
commandUid
receiptState = ACCEPTED | DUPLICATE_ACCEPTED | REJECTED
errorCode
edgeBootId
```

- 物理进展和业务结果统一通过可靠事件；
- OneNet 消息 ID、MQTT Packet ID 和 Pulsar ID 只作诊断。

`START_DELIVERY_SESSION` 保存完整 session 后启动整场投递；同一 session 内的“继续投递”
只在设备本地控制投递门，不生成 OneNet 事件或新的云端授权。`SAMPLE_FULLNESS` 必须携带
后端已经建立的 `detectionUid`；投递结束时也可以由唯一完成事件携带最终现场样本并让
后端原子建立检测身份和 gate，香橙派不能自行创造其他权威检测。

### 7.2 规范正文

一条 outbox 事件第一次创建时固定：

```text
schemaVersion
eventUid
deploymentCode
edgeEventSequence
eventType
occurredAt
work identity
payload
payloadSha256
```

规范 JSON 字节保存在 SQLite。重试直接复用原字节和摘要，不重新序列化。UTC 不可信时按协议明确标记，不能以设备当前时间生成新的业务身份。

### 7.3 配置双证明

配置应用必须依次形成：

```text
完整配置 SQLite 提交
  → CONFIGURATION_PROGRESS(EDGE_SAVED)
  → MCU staging/摘要校验/COMMIT
  → CONFIGURATION_PROGRESS(APPLIED)
```

香橙派只在 MCU 返回相同版本、完整内容摘要和 MCU 子集摘要后报告 `APPLIED`。半份配置、仅 UART ACK 或仅 OneNet 下发成功不能推进终态。

## 8. COS 照片队列

本地路径：

```text
<persistentRoot>/photos/{workType}/{workUid}/{slot}/{photoUid}.jpg
```

对象 key：

```text
ecobin/{deploymentCode}/{workType}/{workUid}/{slot}/{photoUid}.jpg
```

要求：

- `photoUid` 在拍照前生成并持久化；
- 同一作业同一标准槽位最终只有一个业务照片身份；投递作业只有整场首次开门前和最终关门后的四个槽位；
- 临时 COS 凭证只在内存，绝不进入 SQLite、摘要或日志；
- 上传保存 SHA-256、大小、拍摄时间、最终 URL；
- 授权失效时沿原作业建立 `PHOTO_UPLOAD_GRANT_REQUESTED`；
- 完成事件允许槽位为 `UPLOAD_PENDING`，投递订单、已完成清运记录和投递返现都不等待照片；
- 连续失败 72 小时后，先可靠建立 `PERMANENTLY_MISSING` 事件，再删除二进制；
- 上传成功仍不能删除文件，必须等待后端确认该槽位已关联；
- 后端只接受精确 bucket/region/部署/作业/槽位前缀内的 HTTPS URL。

相机或网络故障进入设备异常/告警，不进入用户投递异常。

## 9. UART Registry

### 9.1 单一来源

`contracts/uart/` 下建立机器可读 Registry，至少生成或校验：

- C 与 Python 的消息类型、字段偏移、长度和枚举；
- CRC16-CCITT-FALSE；
- UUID、带符号克、定点单价和时间单位；
- capability bitmap；
- ACK/NACK 错误码；
- 黄金字节样本。

消息族：

```text
控制：HELLO / HELLO_ACK / ACK / NACK / QUERY_STATE / SAFE_CLOSE（仅适用于可由 MCU 关闭的投递门）
配置：CONFIG_BEGIN / DEVICE_BLOCK / PORT_BLOCK / COMMIT / CONFIG_APPLY_RESULT
作业命令：投递 session 开始、清运开始/再次解锁/解锁前结束、满溢采样、基准测量
作业事件：投递首重/最终重量/结束、清运解锁动作/人工完成确认、满溢与基准结果
安全/故障：满溢结果、基准结果、FAULT_OBSERVED、SAFETY_SENSOR_EVENT
状态：STATE_SNAPSHOT_BEGIN / STATE_SNAPSHOT_PORT / STATE_SNAPSHOT_END
```

消息号和字段数值由同一 Registry 冻结，不能由 MCU 与 Python 各自手写。

### 9.2 一致快照分段

I-050 要求一个快照同时覆盖整机和最多六个投口，单帧 payload 上限 242 字节。采用：

```text
STATE_SNAPSHOT_BEGIN
STATE_SNAPSHOT_PORT × portCount
STATE_SNAPSHOT_END
```

规则：

1. `QUERY_STATE` 携带稳定 `snapshotUid`，默认复用该查询的 `mcuCommandUid`。
2. MCU 接收时冻结一次逻辑快照；发送期间的状态变化不修改该快照。
3. `BEGIN` 包含快照/boot、协议固件、当前作业、配置摘要、投口数、总段数、待确认事件范围、捕获 uptime 和复位原因。
4. 每个 `PORT` 包含同一快照/boot、`partIndex/partCount`、唯一投口号和投递门、重量、
   红外、烟雾、故障状态；清运门只能报告电磁阀通断及独立人工确认，物理门位为
   `UNKNOWN`，不伪造门磁。
5. `END` 包含同一身份、总段数和 `snapshotSha256`；摘要覆盖按 `partIndex` 排序的逻辑载荷。
6. 每段仍是独立 UART 1.0 帧，具有 CRC、ACK 和 MCU 事件身份。
7. 香橙派逐段提交 SQLite 后才 ACK；全部索引齐全、身份一致、摘要匹配且 END 到达后，才在一个事务中应用完整快照。
8. 中断、缺段、同索引异内容或 boot 改变时整份作废；不能用部分投口事实解除安全锁。
9. 同一查询重试时 MCU 重放同一冻结快照；MCU 重启后必须使用新的查询 UID。

这是 I-050 线级实现细化，不放开任意消息的通用分片。

## 10. MCU HITL 交付物

固件由其他负责人实施。其任务输入必须包含 Registry 版本、状态机和验收脚本；输出必须包含固件版本、能力位和真机证据。

负责人需交付：

1. Registry 消息号、字段偏移、能力位和错误码确认；
2. 严格分帧、CRC、100ms 帧期限、512 字节缓冲和停等 ACK；
3. 可跨看门狗/断电保存的：
   - `mcuBootId`；
   - 关键事件队列及 `mcuEventSequence`；
   - 当前作业/门阶段；
   - 配置版本/摘要；
   - 危险命令去重墓碑；
4. 配置 staging 与 COMMIT 原子切换；
5. 投递 session、本地继续、清运解锁/再次解锁、满溢、基准、投递门 SAFE_CLOSE 和状态查询状态机；
6. 投递门门磁/执行器、清运电磁阀、称重、红外、烟雾的状态/健康/故障映射；清运
   电磁阀通断不能推定物理门位，人工关门确认独立映射；
7. 稳定重量算法、量程、校准版本和明确失败状态；
8. MCU 对投递门独立超时关门；清运门无自动关门能力，失联后保持原操作等待人工恢复；
9. STATE_SNAPSHOT 冻结、分段、摘要和重放；
10. 非易失介质、容量、擦写寿命和关键事件保留能力说明。

如果 MCU 无法持久保存不可逆动作所需的关键事件/去重信息，则 I-047/I-050 尚未满足，不能由香橙派猜测补齐。

## 11. 恢复顺序

香橙派启动固定执行：

```text
校验 SQLite/部署身份
  → UART HELLO
  → QUERY_STATE 完整分段
  → 收取并持久 ACK MCU 待确认事件
  → 对照 SQLite 作业、boot、门和阶段
  → 必要时只对投递门 SAFE_CLOSE；清运门转人工恢复
  → 收敛原作业或锁存故障
  → READY
```

判断矩阵：

| 场景 | 处理 |
|---|---|
| MCU boot 未变、作业一致 | 先补事件，不重发已导致开门的 START |
| MCU boot 改变 | 投递门逐门证明关闭；投递结果待核查；曾可能解锁的清运进入人工恢复 |
| SQLite 与 MCU 作业不一致 | 整机安全锁，禁止按时间选择“较新一方” |
| 投递门打开或未知 | 保留作业/占位，等待 MCU 独立关门或发送新 SAFE_CLOSE |
| 清运电磁阀为通电/未知，或曾解锁但无人确认关门 | 保留原清运操作和阻断，等待现场人工确认；断电不能证明门扇关闭 |
| 双端断电 | MCU 新 boot 先关闭投递门并停止清运解锁输出；香橙派按原 SQLite 恢复，绝不自动重放开门/解锁 |
| MQTT 断网 | 已授权投递 session 可在本地继续并安全结束、保存唯一 outbox；不创建新的云端作业 |
| SQLite 损坏/丢失 | 停止新作业，保留设备故障，不重建身份和序号继续运营 |

## 12. 超时与保留

| 项目 | 冻结值/来源 | 行为 |
|---|---|---|
| UART ACK | 500ms，最多发送 3 次原帧 | 之后 QUERY_STATE；投递门可 SAFE_CLOSE，清运解锁结果未知则转人工恢复 |
| UART 帧接收 | 100ms | 候选帧超时后严格重同步 |
| 投递/清运首次执行授权 | 后端提交后 60 秒 | 只限制首次物理开始 |
| 投递结果 | 首个可信物理进展后 15 分钟 | 超时进入结果待恢复，不伪造订单 |
| 继续选择 | 配置默认 30 秒 | 设备本地单调时钟为权威；超时使用最后可靠关门重量正常结束整场 |
| 清运操作 | 配置默认 30 分钟 | 解锁可能发生后必须由原清运员人工恢复；不创建新操作 |
| 照片失败 | 72 小时 | 先建立永久缺失事实再删文件 |
| 未业务确认事件 | 无年龄上限 | 永不按时间自动删除 |

## 13. 设计追踪项（已映射到正式任务）

以下本章编号只用于覆盖追踪；正式任务、依赖和状态见
[`p0-controlled-loop`](../tasks/p0-controlled-loop/00-index.md)。

| 草案 ID | 标题 | 类型 | 依赖 | 验收结果 |
|---|---|---|---|---|
| EDG-01 | OneNet Schema 与 UART Registry 首版 | AFK + HITL 数值冻结 | 冻结接口 | Schema/Registry 可解析，旧消息不在注册表。 |
| EDG-02 | MCU 协议适配基础层 | HITL | EDG-01 | 当前固定帧线路、屏幕状态机和物理行为真机通过；选择 `uart-v1` 时再验证 HELLO/CRC/ACK。 |
| EDG-03 | 香橙派 SQLite 与恢复骨架 | AFK | 无 | 强杀进程后命令、outbox、照片、确认和序号不丢。 |
| EDG-04 | 香橙派 MCU 协议适配与恢复 | AFK + HITL 联调 | EDG-01～03 | 显式协议模式下噪声、拆粘包、重复、重启不重复动作；缺失事实不伪造。 |
| EDG-05 | OneNet inbox、outbox 与业务确认 | AFK + HITL | 中心 FND-05、EDG-03 | 设备受理、物理结果、业务结果分层且确认可重发。 |
| EDG-06 | COS 持久照片队列 | AFK + HITL | EDG-03、授权端口 | 四图/缺图、过期补授权、重启补传和确认后删除闭环。 |
| EDG-07 | 投递真机纵向切片 | AFK + HITL | EDG-02～06、后端投递 | 一次 session 只上报一次并建一单；本地继续、负重量标志和超时结束可靠。 |
| EDG-08 | 清运真机纵向切片 | AFK + HITL | EDG-02～06、后端清运 | 换袋、再次解锁、人工关门确认、单一完成事件和基准处理。 |
| EDG-09 | 配置、满溢与恢复 | AFK + HITL | EDG-02～08 | 配置双证明、三种满溢模式和重启恢复通过。 |
| EDG-10 | 旧链切断与人工验收 | HITL | EDG-01～09 | 旧 D1/旧业务处理不触发动作；当前固定帧只能经显式适配器工作，真机证据完整。 |

## 14. 主审否决项

- OneNet/Pulsar 业务处理失败后仍 ACK；
- 下行配置缺失或失败时记录占位成功；
- 日志输出完整 OneNet input、微信正文或 COS 临时凭证；
- 生产继续允许 Pulsar 不安全 TLS；
- MQTT QoS 0、内存作业或仅凭 PUBACK 删除可靠事实；
- 香橙派回调线程同步等门、重量、拍照或 COS；
- 固定帧缺失 CRC/ACK/业务身份时伪造相应事实，或用两种解析器自动探测/失败回退；
- 重量失败上报 0、负重量截断为 0；
- 投递和清运使用不同整机锁；
- `/tmp` 照片、覆盖式文件名、永久 COS 密钥；
- 用部分 STATE_SNAPSHOT 解除安全锁；
- 重启后自动重放旧开门命令；
- 把投递中间轮次、照片、重量或继续按钮上传 OneNet；
- 把清运电磁阀断电说成门磁已经证明真实关门，或对清运门调用 SAFE_CLOSE；
- 进程内模拟分支仍连接真实 MQTT/COS，或在边界不清时可能触发真实设备；
- 以 I-055 延后自动 CI/HIL 为由延后 CRC、门安全、断电恢复和必要真机人工验收。
