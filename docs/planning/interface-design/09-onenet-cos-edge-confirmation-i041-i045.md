# EcoBin P0 目标接口设计：OneNet、COS、边缘持久化与业务确认（I-041～I-045）

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-041～I-045 已确认；2026-07-24 已按会话一单、最终一次上报及清运锁推定状态修订**
>
> 说明：本文件定义 OneNet 可信上行、服务下行、香橙派 SQLite、COS 直传和后端业务确认的目标契约。它承接 I-016～I-030 已冻结的设备配置、投递、清运、满溢和恢复语义，不修改那些业务状态机。
>
> 切换边界：当前 [`docs/iot/onenet-thing-model.md`](../../iot/onenet-thing-model.md)、对应 JSON 物模型、香橙派实现和匿名 `/api/iot/**` 只代表现状，不满足本章目标。正式切换必须同步替换物模型、后端适配器、边缘实现和契约样本；生产链路不做旧新写协议并行兼容。

## 本章统一边界

1. P0 正式 IoT 链路固定为：

   ```text
   上行：香橙派 → OneNet MQTT → OneNet 北向 Pulsar → EcoBin 后端
   下行：EcoBin 后端 → OneNet 设备服务调用 → 香橙派
   照片：香橙派 → COS HTTPS 直传；后端只发受限临时授权并保存关联事实
   ```

   EcoBin 不要求 OneNet 或香橙派向中心服务器发起公网 HTTP 回调；生产环境也不开放以设备序列号自报身份的匿名直连接口。
2. 一条链路至少区分四层结果：
   - **传输已接受**：MQTT/Pulsar/OneNet HTTPS 接受了消息；
   - **设备已受理**：香橙派校验并可靠保存了命令；
   - **物理事实已发生**：门、称重、照片或配置应用产生了可信设备证据；
   - **业务已完成**：后端权威事务已经建立订单、换袋、配置应用或其他目标事实。

   前一层不能自动代表后一层。尤其 OneNet 服务调用返回 `code=0` 只说明该次服务调用被平台接受，不等于设备 ACK、门已动作或业务已完成。
3. 业务身份只能来自后端已经建立的稳定 ID：`deploymentCode`、`applicationUid`、`sessionUid`、`operationUid`、`detectionUid`、`measurementUid`、`commandUid` 和 `eventUid`。投递会话不再分配第二作业身份。OneNet 消息 ID、Pulsar 消息 ID、MQTT Packet ID、HTTP `request_id`、时间戳或数据库主键都只能作为传输诊断，不能代替这些身份。
4. 所有机器消息采用版本化、可生成代码且可机器校验的 Schema。物模型中的每个命令和事件使用自己的强类型参数结构，不能用一个任意 JSON 字符串、可变键值 Map 或通用 `payload` 字符串承载全部业务。
5. 设备只报告现场事实和后端先前授权给它的稳定身份，不能自报租户、机构、用户、钱包、单价、返现金额或审核结果。后端从 OneNet 可信外层设备身份解析物理资产及部署，再从数据库原作业恢复租户、机构、用户和配置快照。
6. 重量在线协议一律使用带符号整数克，金额不进入设备协议；时间使用 UTC RFC 3339 字符串。设备侧使用 Python 3.11 可实现的类型和算法，不依赖 Python 3.14 专属语法。
7. 凭证与业务载荷分离。设备 Key、OneNet 产品 AccessKey、COS 临时密钥、永久密钥和秘密引用不得进入业务命令稳定摘要、数据库执行快照、普通日志、审计正文、告警正文或 API 响应。
8. 本章只冻结语义、字段和收敛边界；OneNet 物模型 JSON、JSON Schema、Pulsar 解密适配、SQLite DDL 和正式 UART 二进制帧在实施/详细设计中生成。首版可以按各端进度用人工样例分批联调，完整跨语言契约 CI/HIL 门禁按后确认的 I-055 在共同首版形成后引入。

## I-041 OneNet 可信上行与统一事件封套

**已确认：可靠设备事实必须先在香橙派持久化，使用部署内全局单调序号和稳定事件 ID 上报；后端完成可信收件后才确认 Pulsar 传输，完成权威业务事务后才返回 I-045 的业务确认。**

### 1. 事件封套

以下 JSON 只展示语义结构；实际 OneNet 物模型为每个 `eventType` 定义强类型参数，而不是声明一个任意 JSON 对象：

```json
{
  "schemaVersion": 1,
  "eventUid": "2c496616-9858-4a58-af99-8fa0cb6ade6d",
  "deploymentCode": "Dp_7fQw...",
  "edgeEventSequence": 1042,
  "eventType": "CLEAN_COMPLETE",
  "deliveryClass": "RELIABLE_FACT",
  "target": {
    "type": "CLEAN_OPERATION",
    "uid": "294869f9-38e9-48ea-b7d7-7b20236c12c5"
  },
  "commandUid": "03fceaa1-efc9-44d9-8504-d8f81bf3fe8c",
  "occurredAt": "2026-07-23T05:41:08.274Z",
  "clockQuality": "SYNCED",
  "payloadSha256": "b7d0...64位小写十六进制",
  "payload": {
    "typedFields": "由事件专属 Schema 定义"
  }
}
```

字段规则如下：

- `eventUid` 是香橙派在首次写入本地事件时生成并永久固定的 UUIDv4；同一事实重试必须复用原值。
- `deploymentCode` 是后端下发并由边缘当前上下文保存的部署身份。它不能从硬件 SN、机构、OneNet 消息 ID或本地时间推导。
- `edgeEventSequence` 是正的 64 位整数，由同一个 `deploymentCode` 下所有强类型边缘事件共享一个分配器，在创建事件的同一 SQLite 事务中原子加一。不能按事件类型、投口或作业分别从 1 开始。
- `deliveryClass` 只用于显式表达交付策略，后端仍以固定的 `eventType → deliveryClass` 注册表校验，不能信任设备自行把可靠事实降级为遥测。
- `target` 必须命中该事件类型允许的一个强类型业务目标；不使用任意表名或可反射更新的资源名称。没有作业目标的设备运行/故障事件以 `DEVICE_DEPLOYMENT + deploymentCode` 为目标。
- `commandUid` 只在事实源自某个后端命令时必填；设备自主产生的运行故障和照片授权请求可以为空。投递继续/结束选择是本地事实，不建立 OneNet 事件。
- `occurredAt` 只有设备可信 UTC 已同步时才给出；无法给出可信绝对时间时为 `null`，并令 `clockQuality=ESTIMATED/UNAVAILABLE`。后端接收时间不能伪装成设备发生时间。开始授权、投递本地选择窗口和设备安全超时仍以本地持久化的单调时钟截止点执行。
- `payloadSha256` 是按 RFC 8785 JSON Canonicalization Scheme 对事件专属 `payload` 的 UTF-8 规范字节计算的 SHA-256。数值字段使用整数或已冻结格式的字符串，禁止跨语言二进制浮点参与摘要。
- 后端另计算不可变 `canonicalSha256`，绑定可信 OneNet 来源、Schema 版本、事件类型、部署、序号、目标、命令和 `payloadSha256`。它用于识别“同一事件 ID 或序号却出现不同内容”，不由设备自报。

### 2. 序号、重复与部署代际

- 后端允许 OneNet/Pulsar 乱序、重复和短暂缺号；不能因为先收到序号 1042、后收到 1041 就丢弃 1041，也不能把缺号直接解释成业务失败。
- 同一 `eventUid + canonicalSha256` 重复到达时返回既有收件/处理结果；同一 `eventUid` 不同摘要进入隔离。
- 同一 `(deploymentCode, edgeEventSequence)` 被不同 `eventUid` 或不同内容复用时进入隔离并锁存设备协议一致性故障。后端不能采用“最后到达覆盖旧值”。
- SQLite 丢失、重装或损坏后，不允许同一 `deploymentCode` 自动把序号重置为 1。设备必须停止新开门并进入受控重投产；恢复原序号或由平台建立新的部署代际后才可继续。P0 不设计自动猜测序号的旁路。
- 迟到事件若携带原作业稳定身份，且可信 OneNet 物理设备仍能映射到发生时的同一部署/资产，可以归入已经结束的旧部署历史；没有原作业目标的运行快照只能作用于当前部署，不能把旧机构状态写到新机构。

### 3. P0 事件注册表

| `eventType` | `deliveryClass` | 目标 | 核心语义 |
|---|---|---|---|
| `DEVICE_COMMAND_OBSERVED` | `RELIABLE_FACT` | 设备命令 | 命令已受理、明确拒绝、开动前失败或其他命令类型允许的强类型进度；不得承载投递中间门/重量/按钮过程 |
| `CONFIGURATION_PROGRESS` | `RELIABLE_FACT` | 配置应用 | 精确证明 `EDGE_SAVED/APPLIED/FAILED`，携版本和配置摘要 |
| `DELIVERY_COMPLETE` | `RELIABLE_FACT` | 投递会话 | 整场唯一完成事件：首次开门前/最终关门后重量、四个整场照片槽、`negativeWeightAnomaly`、冻结配置摘要及最终投递门状态 |
| `CLEAN_COMPLETE` | `RELIABLE_FACT` | 清运操作 | 完整换袋、带符号新旧重量、清运员确认、电磁阀断电、由锁状态推定的关闭状态和照片槽 |
| `FULLNESS_SAMPLE_COMPLETE` | `RELIABLE_FACT` | 满溢检测 | 一次初检、复检或人工重检的强类型传感器结果 |
| `BASELINE_MEASUREMENT_COMPLETE` | `RELIABLE_FACT` | 空袋基准重测 | 当前袋现场稳定总重量或明确失败 |
| `DEVICE_FAULT_OBSERVED` | `RELIABLE_FACT` | 设备部署/投口 | 新故障或故障加重的不可丢事实 |
| `DEVICE_FAULT_RECOVERED` | `RELIABLE_FACT` | 设备部署/投口 | 设备已真实报告恢复；不替代工作人员现场确认 |
| `PHOTO_STATUS_REPORTED` | `RELIABLE_FACT` | 原投递会话或清运操作 | 补齐照片或可靠声明永久缺失 |
| `PHOTO_UPLOAD_GRANT_REQUESTED` | `RELIABLE_FACT` | 原投递会话或清运操作 | 原授权失效或重启后请求新的同范围 COS 临时授权 |
| `BUSINESS_CONFIRMATION_RECEIPT` | `CONTROL_RECEIPT` | 原确认 | 香橙派已持久化 I-045 确认；用于停止后端确认重试 |
| `DEVICE_RUNTIME_SNAPSHOT` | `TELEMETRY_SNAPSHOT` | 当前部署 | 心跳、版本、门/传感器当前诊断快照；不代替上述可靠事实 |

`RELIABLE_FACT` 必须进入 SQLite 发件箱并重试到收到 I-045 确认；`CONTROL_RECEIPT` 必须可靠保存到收到 MQTT QoS 1 的 PUBACK，但不会再触发另一层业务确认，防止无限确认循环。`TELEMETRY_SNAPSHOT` 可以在边缘合并旧快照，并在 MQTT QoS 1 PUBACK 后清理；它仍须通过可信收件和 Schema 校验，但不要求逐条业务确认。

照片不完整、一次心跳丢失和普通遥测波动不能伪装成作业异常。影响订单、换袋、配置应用、容量 gate 或严重安全恢复的事实必须使用对应可靠事件，不能只放在可合并心跳里。

投递会话成功开始后，`DELIVERY_COMPLETE` 是唯一投递业务上行。继续/结束按钮、中间开关门、中间称重、中间照片和中途普通满溢值不得注册为 OneNet 事件或塞入 `DEVICE_COMMAND_OBSERVED/DEVICE_RUNTIME_SNAPSHOT`。开始命令在首次开门前明确拒绝时可以使用命令拒绝事实安全释放；硬安全故障继续使用独立设备故障事件，但不得携带中间投递重量。

### 4. OneNet 北向收件与 Pulsar ACK

后端收件顺序固定为：

1. 使用部署环境中的 OneNet 北向凭证接收并按官方协议认证/解密消息，读取可信外层 `productId + deviceName`；
2. 校验产品 ID 等于当前环境配置，按 `deviceName=hardwareSn` 定位平台资产，并核对事件 `deploymentCode`、强类型目标和资产历史关系；
3. 校验事件类型、Schema、字段范围、UUID、规范摘要和来源作用域；
4. 在同一 MySQL 事务写 `ops_inbox_message`、规范事件头和唯一 `PROCESS_INBOX` 可靠任务；
5. 事务提交后才向 Pulsar ACK；后续业务处理失败由本地可靠任务恢复，不重新依赖 OneNet 保留同一传输消息。

处理规则如下：

| 情况 | 持久化与 Pulsar 处理 |
|---|---|
| 同一事件、同一规范内容重复 | 命中原 inbox/规范事件或追加传输观察，事务提交后 ACK |
| 同一事件 ID 不同内容、部署序号冲突、可信来源下不支持的 Schema、永久不合法目标 | 写脱敏隔离记录和必要告警，事务提交后 ACK；不得无限毒化消费分区 |
| 数据库、秘密设施、证书或其他暂时性依赖不可用 | 不 ACK 或按消费者策略 negative ACK，等待原消息重投 |
| 来源无法认证/解密 | 不形成可信 inbox 或设备事实；只写脱敏安全诊断/聚合告警，并按北向适配器的安全失败策略处理 |

OneNet/Pulsar 的消息 ID 只保存在 `ops_inbox_message` 传输元数据中。当前用 OneNet 消息 ID 兜底生成业务事件身份的实现必须移除。

## I-042 OneNet 强类型下行、命令受理与进度

**已确认：一个稳定领域命令在数据库中只有一个 `commandUid` 和语义摘要；OneNet 调用次数、临时凭证和设备观察都是它的执行证据，不能生成第二个业务命令或改变原目标。**

### 1. 通用命令元数据

以下仍是语义展示；正式物模型以每种服务的强类型 `params` 表达：

```json
{
  "schemaVersion": 1,
  "commandUid": "03fceaa1-efc9-44d9-8504-d8f81bf3fe8c",
  "commandType": "START_DELIVERY_SESSION",
  "deploymentCode": "Dp_7fQw...",
  "target": {
    "type": "DELIVERY_SESSION",
    "uid": "01baf1a9-83bb-475d-b1e5-c2f60878c4cb"
  },
  "issuedAt": "2026-07-23T05:40:00.000Z",
  "expiresAt": "2026-07-23T05:41:00.000Z",
  "payloadSchemaVersion": 1,
  "payloadSha256": "1b4a...64位小写十六进制",
  "payload": {
    "typedFields": "由命令专属 Schema 定义"
  },
  "cosGrant": null
}
```

- `commandUid` 由后端业务事务生成并固定；可靠任务重试和 OneNet 服务调用重试必须复用。
- `target.type + target.uid` 必须与 `commandType` 的注册类型、数据库强类型外键和冻结业务快照一致。
- `expiresAt` 是设备能否首次执行该命令的绝对截止点。投递开门、清运开始/恢复固定为后端事务提交后 60 秒；已经在期限内开始的清运仍按原操作执行时限完成。
- `payloadSha256` 对命令专属稳定业务载荷计算；后端保存的命令 `canonicalSha256` 还绑定 Schema、命令类型、部署、目标、签发和截止时间。
- `cosGrant` 是实际发送尝试临时附加的执行资料，不属于稳定命令载荷或摘要。刷新临时凭证不能改变 `commandUid`。

### 2. P0 下行注册表

领域设备命令如下：

| `commandType` | 强类型目标 | 说明 |
|---|---|---|
| `APPLY_CONFIGURATION` | 配置应用 | 下发完整版本、Schema 和内容摘要，要求分别证明边缘落盘和 MCU 应用 |
| `START_DELIVERY_SESSION` | 投递会话 | 可靠保存整场会话及冻结快照，取得首次开门前照片/稳定重量后授权整场；继续投递不再建立云端命令 |
| `START_CLEAN_OPERATION` | 清运操作 | 可靠保存完整清运上下文、取得真实首次解锁前稳定重量后才允许电磁阀首次通电 |
| `END_CLEAN_BEFORE_UNLOCK` | 清运操作 | 仅在首次解锁命令尚未可能执行时请求停止，最终仍需可信“电磁阀从未通电且无在途解锁”证据 |
| `RESUME_CLEAN_OPERATION` | 清运操作 | 原清运员恢复同一操作，使用新的恢复序号和执行窗口 |
| `SAMPLE_FULLNESS` | 满溢检测 | 执行该检测指定的初检、确认复检或人工重检 |
| `MEASURE_EMPTY_BAG_BASELINE` | 基准重测 | 对当前袋执行真实稳定总重量测量 |

清运过程中的“再次开门”实际是同一 `operationUid` 下再次给电磁阀通电；香橙派先持久化新的本地 `mcuCommandUid`，再交给 MCU 执行。它不创建新的云端业务作业。只有超时/重启进入 `RECOVERY_REQUIRED` 后的原清运员恢复才建立新的 `RESUME_CLEAN_OPERATION` 领域命令；恢复命令本身不自动通电。

以下是协议控制下行，不是物理领域命令，也不写 `dev_device_command`：

| 控制类型 | 目标 | 执行记录 |
|---|---|---|
| `CONFIRM_EDGE_EVENT` | 原 `eventUid` | 唯一可靠任务 `CONFIRM_EDGE_EVENT:<eventUid>` |
| `PROVIDE_PHOTO_UPLOAD_GRANT` | 原照片授权请求 | 唯一可靠任务 `PROVIDE_PHOTO_UPLOAD_GRANT:<grantRequestEventUid>` |

两类控制消息同样使用稳定身份、Schema 和设备回执，但不能占用投递/清运物理命令槽，也不能被设备解释为开门授权。

### 3. OneNet 服务调用与分层结果

可靠执行器按当前本地官方快照 [`设备服务调用.md`](../../references/设备服务调用.md) 调用 OneNet `thingmodel/call-service`，使用环境注入的产品级鉴权材料；设备名由硬件 SN 确定。每次调用形成新的技术 attempt，可以有不同 OneNet `request_id`，但命令身份和稳定摘要不变。

```text
OneNet HTTP code=0
  ≠ 香橙派已经收到
  ≠ 香橙派已经可靠保存
  ≠ MCU 已受理
  ≠ 门已打开/关闭
  ≠ 本地结果已保存
  ≠ 后端业务已完成
```

OneNet 返回值和服务回复只保存为传输证据。设备按命令类型通过 I-041 的 `DEVICE_COMMAND_OBSERVED` 或对应更强完成事件报告规范事实：

```text
RECEIVED
ACCEPTED / REJECTED
MCU_ACCEPTED
PRE_START_FAILED
FAILED
```

并非每个命令都需要全部阶段；机器事件 Schema 按命令类型限制合法阶段。例如配置使用 `CONFIGURATION_PROGRESS`，满溢采样可以直接以完整可信结果越过丢失的中间观察。成功投递会话不上传开门、关门、继续、结束或本地轮次结果观察，只上传最终 `DELIVERY_COMPLETE`；清运锁通断也不得转换成虚构的 `DOOR_OPENED/DOOR_CLOSED`。

### 4. 香橙派受理规则

香橙派收到服务下行后必须依次：

1. 校验 Schema、`deploymentCode`、命令类型、目标、字段范围、稳定摘要和绝对截止时间；
2. 将命令和完整业务上下文写入 SQLite，建立本地单调时钟截止点并提交；
3. 提交成功后才产生 `ACCEPTED` 事件和允许后续 MCU 动作；
4. 每个不可逆物理阶段先取得 MCU/传感器可信结果，再与本地状态原子保存并上报。

相同 `commandUid + canonicalSha256` 重复下行只返回原受理/执行进度，不重复开门、应用配置、采样或建立本地作业。相同 `commandUid` 不同摘要必须拒绝、持久化协议冲突并停止该命令；临时 `cosGrant` 更新不参与该冲突判定。

设备必须永久拒绝：

- 低于本地最高已接受版本的配置；
- 已过 `expiresAt` 才首次到达或尚未受理的开门/开始/恢复命令；
- 部署、目标或摘要不匹配的命令；
- 本地业务数据无法可靠落盘时的任何新作业；
- 旧命令可能与已恢复的新物理上下文竞争的情况。

UTC 失准会阻止接收新的限时开门授权；已经在可信时间下受理并持久化单调截止点的在途作业仍以单调时钟安全结束和上报，不能因校时跳变延长授权。

## I-043 香橙派 SQLite、断电恢复与本地清理

**已确认：SQLite 是设备业务状态和可靠发件箱的唯一结构化恢复来源；内存、日志、当前屏幕、OneNet 在线属性或文件名都不能重建已授权作业。**

### 1. 逻辑数据集合

正式 DDL 可以拆表，但至少必须表达下列独立事实：

| 逻辑集合 | 最小责任 |
|---|---|
| `edge_meta` | 当前 `deploymentCode`、协议版本、已应用配置版本/摘要、全局下一事件序号和存储 Schema |
| `command_inbox` | 稳定命令、规范摘要、目标、截止时间、受理/拒绝及执行进度 |
| `delivery_session` | 当前投递会话、整场授权/冻结摘要、首次开门前与最终关门后结果、本地轮次恢复数据、负重量异常锁存值、选择窗口及最终事件状态；不存在周期集合 |
| `clean_operation` | 原操作、新旧袋/基准快照、首次解锁前重量、恢复代际、电磁阀通断、由锁状态推定的门状态、清运员最终确认和最终称重 |
| `detection_context` / `measurement_context` | 满溢采样和空袋基准重测的目标、代际与结果 |
| `event_outbox` | I-041 可靠事件完整内容、全局序号、重试和业务确认状态 |
| `photo_outbox` | 照片身份、原作业/槽位、本地路径、摘要、大小、上传/关联状态和失败期限 |
| `confirmation_inbox` | I-045 确认身份、原事件/摘要、持久化和回执状态 |
| `idempotency_tombstone` | 已清理命令、事件、确认和照片的稳定 ID/摘要，用于拒绝旧 ID 冲突和安全重放 |

投递、清运、检测和测量使用各自的强类型状态，不以一个可任意覆盖的通用 JSON 状态行代替。OneNet 设备 Key 等长期凭证由受控设备配置设施保存，不进入这些业务表；COS 临时凭证只在内存中使用，重启后重新申请。

### 2. 必须原子的本地边界

- 收到命令时，“命令正文/摘要 + 业务上下文 + 本地截止点”先提交，之后才能报告 `ACCEPTED`。
- 收到 `START_DELIVERY_SESSION` 时先原子保存会话、整场冻结摘要、首次执行截止点和四个照片槽，再取得首次开门前照片与稳定重量；只有这些事实可靠落盘后才允许首次开门。
- 每次继续点击、投递门动作和轮次称重只更新同一 `delivery_session` 的本地恢复状态。它们不分配 `eventUid/edgeEventSequence`、不进入 OneNet 发件箱；任一轮次达到负重量阈值时只把锁存布尔值从 `false` 改为 `true`。
- 用户结束或 30 秒窗口届满时，把最终关门后重量、四个整场照片槽、冻结摘要、最终门状态、结束原因和 `negativeWeightAnomaly` 与唯一 `DELIVERY_COMPLETE` 发件箱行原子提交。中间数据不复制到该事件。
- 清运首次解锁前，先保存完整操作上下文和执行期限，再从 MCU 取得本次真实稳定总重量，并把重量和可靠性提交；完成这些写入后才允许 MCU 给电磁阀通电。
- 门动作、称重、最终结果和照片清单必须先形成可恢复的本地事实；完整事件、`edgeEventSequence` 分配及发件箱行在同一事务提交后才发布 MQTT。
- 一张照片只有在临时文件完整写入、同步并在同一持久文件系统原子改名后，才能登记为可上传文件。SQLite 不存照片 BLOB，照片不放 `/tmp`。
- 收到业务确认时，“验证原事件/摘要 + 标记已确认 + 保存确认 + 建立回执”在同一事务完成，之后才允许回收原事件载荷。

SQLite 的 WAL、外键、忙等待、同步级别、连接所有权、目录同步和文件 `fsync` 时点在设备详细设计冻结；无论采用何种参数，都必须通过强制结束进程、随机断电、磁盘满和重启测试证明以上事务边界。

### 3. 重启与故障恢复

香橙派启动顺序固定为：

1. 打开并校验 SQLite Schema、完整性、部署身份及持久目录；
2. 查询 MCU 当前协议/固件、全部投递门真实状态、清运电磁阀通断和关键传感器状态；不得把锁通断解释成清运门物理检测；
3. 恢复未确认命令、作业、事件、照片和业务确认；
4. 先收敛投递门安全、清运锁断电、原作业结果和可靠上报，再决定是否接受新作业。

重启后禁止仅根据“数据库里命令未完成”重新开门。投递门已真实安全关闭、清运电磁阀已断电且原上下文完整时，可以继续：

- 重发同一可靠事件；
- 补传原照片；
- 保留原清运操作并等待原清运员恢复/确认；
- 报告明确的本地恢复或失败证据。

投递门打开/未知、清运电磁阀仍通电、MCU 不可达、SQLite 校验失败、业务行与文件冲突或无法可靠写入时，整机保持安全锁并拒绝新的投递和清运。香橙派可以请求关闭有真实执行器的投递门并使清运锁断电，但 `SAFE_CLOSE` 不适用于清运门，也不能删除上下文或用锁断电伪造人工关门。

SQLite 写失败、损坏或磁盘不足时：

- 已经发生的物理事实尽最大可能保留为只读证据并在存储恢复后上报；
- 未开始的新作业一律拒绝；
- 不能从当前 OneNet 属性、服务器时间、当前重量或新用户会话猜造丢失的原作业；
- 以稳定设备故障事件和聚合告警暴露问题，按既有 80% 告警、90% 停止新投递及关键写入能力规则处理。

### 4. 本地保留与清理

- 未获 I-045 业务确认的 `RELIABLE_FACT`、其目标上下文和必要诊断不得按时间自动删除。
- 业务确认及其回执完成后，事件大载荷可以进入分阶段压缩/清理；已确认结构化作业记录仍本地保留 7 天，期满才可删除。
- 清理后至少保留稳定 ID、规范摘要、部署和终态墓碑，使迟到命令、事件或确认仍能得到原幂等结果，而不是重新执行。
- 照片二进制遵守 I-044 的独立规则：成功上传还不够，只有后端确认已经关联到原作业槽位后才立即可删；持续失败 72 小时后先可靠建立永久缺失事实再删除。
- 遥测快照可以合并和按容量策略清理，但不得挤占未确认事件、当前作业或安全结果所需空间。任何清理器都不能删除正在事务中使用或尚未确认的可靠事实。

## I-044 COS 临时授权、对象身份与照片补传

**已确认：设备在后端授权的原作业前缀内自行生成对象 key 并直传 COS；照片失败不阻断订单、清运或返现，后端通过独立可靠事实把四个固定槽位补齐或标为永久缺失。**

### 1. 对象 key 和照片身份

每个作业只授权下列前缀：

```text
ecobin/{deploymentCode}/{workType}/{workUid}/
```

其中：

```text
workType = delivery-session | clean-operation
workUid  = sessionUid | operationUid
```

香橙派在拍摄前生成并持久化 UUIDv4 `photoUid`，对象 key 固定为：

```text
ecobin/{deploymentCode}/{workType}/{workUid}/{slot}/{photoUid}.jpg
```

`deploymentCode/workUid/photoUid` 使用既有规范公开值；`workType/slot` 只接受注册表中的小写或固定 ASCII 映射，禁止 `..`、反斜杠、重复分隔符、百分号二次解码和用户输入路径。

四个业务槽位固定为：

| 作业 | 槽位 |
|---|---|
| 投递会话 | `BEFORE_INNER`、`BEFORE_OUTER`、`AFTER_INNER`、`AFTER_OUTER` |
| 清运操作 | `FIRST_OPEN_INNER`、`FIRST_OPEN_OUTER`、`FINAL_CLOSE_INNER`、`FINAL_CLOSE_OUTER` |

同一作业同一槽位最终只有一个业务照片身份。相机重拍可以替换尚未上报的本地候选，但一个 `photoUid` 一旦进入可靠事件就不可被另一文件、URL 或摘要复用；投递中间过程不拍摄/上传业务照片，清运中途再次解锁照片也不进入四个标准槽位。

### 2. 临时授权结构和最小权限

投递会话在 `START_DELIVERY_SESSION`、清运在 `START/RESUME_CLEAN_OPERATION` 实际下行时，可以附加：

```json
{
  "grantUid": "75da5927-2344-4e13-a921-942d107f577f",
  "tmpSecretId": "AKID...",
  "tmpSecretKey": "<仅设备内存>",
  "sessionTokenParts": ["part-1", "part-2"],
  "bucket": "example-1250000000",
  "region": "ap-guangzhou",
  "baseUrl": "https://example-1250000000.cos.ap-guangzhou.myqcloud.com",
  "keyPrefix": "ecobin/Dp_.../delivery-session/01ba.../",
  "expiresAt": "2026-07-23T06:10:00Z"
}
```

- `sessionTokenParts` 是按序拼接的协议字段，只在单个物模型字符串上限不足时拆分；每段最多 512 个字符，整体内容不得被日志、摘要或业务数据库保存。
- 默认签发满足一次作业所需的最短可用有效期；P0 可采用 30 分钟并通过补授权续期，不能为了减少刷新签发长期或永久密钥。
- STS 策略只允许该桶、区域和本作业精确前缀下的 `name/cos:PutObject`；P0 照片采用单次小对象上传，不授予 List、Get、Delete、ACL、跨桶、其他作业前缀或分片上传权限。
- 生成临时密钥的后端 CAM 身份本身也必须使用最小权限。腾讯云 COS 官方文档要求前端直传使用临时密钥并以 `action/resource` 限制授权范围，不能把永久密钥或宽泛资源权限交给客户端；实施时以[临时密钥前端直传安全说明](https://cloud.tencent.com/document/product/436/40265)和[临时密钥生成说明](https://cloud.tencent.com/document/product/436/14048)为准。
- 初始授权必须从后端已经授权的强类型作业生成；设备不能提交任意前缀请求一个通配写凭证。

### 3. 完成事件中的照片槽

`DELIVERY_COMPLETE/CLEAN_COMPLETE` 必须为四个槽位各给一个记录，但不要求四张都已上传：

```json
{
  "slot": "AFTER_INNER",
  "status": "AVAILABLE",
  "photoUid": "436355d5-21ac-49c0-a58a-b082f8854ead",
  "url": "https://example-1250000000.cos.ap-guangzhou.myqcloud.com/ecobin/...",
  "sha256": "5e31...64位小写十六进制",
  "sizeBytes": 483220,
  "capturedAt": "2026-07-23T05:40:59.031Z",
  "missingReason": null
}
```

状态规则为：

| 状态 | 字段与含义 |
|---|---|
| `AVAILABLE` | `photoUid/url/sha256/sizeBytes` 必填，表示设备已成功直传 |
| `UPLOAD_PENDING` | 已拍摄时 `photoUid/sha256/sizeBytes` 必填、`url` 为空；相机尚未形成文件时 `photoUid` 可空并记录安全原因 |
| `PERMANENTLY_MISSING` | `url` 为空、`missingReason` 必填；表示本地已可靠放弃该槽位 |

照片缺失、上传 pending 或后端暂未验证 URL 都不产生用户投递异常，不阻止订单、清运换袋、审核返现或业务确认。后端先为四个槽位建立状态，再异步暴露相机/网络/上传故障。

后端接收 `AVAILABLE` 时校验：

- URL 必须为 HTTPS，host 精确匹配当前环境配置的 COS bucket/region 域名；
- URL 解码一次后的规范 path 必须精确落在该部署、作业和槽位前缀；
- URL 不得带查询凭证、fragment、userinfo 或非默认端口；
- `photoUid`、路径、SHA-256 和大小格式必须合法。

校验通过后原样保存设备回传的最终对象 URL；后端不从订单号推导/重建对象 key，也不在建单事务同步发起 COS `HEAD`。URL 或槽位内容冲突进入隔离，不能覆盖已经成立的 `AVAILABLE`。

### 4. 补授权、补传和永久缺失

授权过期、香橙派重启或初次命令没有可用授权时，设备为原作业及待处理槽位建立唯一 `PHOTO_UPLOAD_GRANT_REQUESTED` 事件。后端必须：

1. 从事件可信来源和强类型目标恢复原部署/作业；
2. 验证该作业确实授权过这些固定槽位，且对象前缀与原身份一致；
3. 建立或复用 `PROVIDE_PHOTO_UPLOAD_GRANT:<grantRequestEventUid>` 可靠任务；
4. 在实际发送时生成新短期凭证，不改写原业务命令或作业。

同一个请求事件重试复用同一任务和授权请求身份；该临时授权过期后设备生成新的请求事件，不能复用过期密钥，也不能创建新的投递/清运作业。

上传成功或永久缺失后，设备使用 `PHOTO_STATUS_REPORTED` 指向原 `sessionUid/operationUid + slot`：

- 同一照片身份、URL 和摘要重复到达幂等返回原关联；
- `UPLOAD_PENDING → AVAILABLE/PERMANENTLY_MISSING` 单调前进；
- 已 `AVAILABLE` 的槽位不接受另一照片覆盖；
- `PERMANENTLY_MISSING` 是设备已经可靠放弃的终态，后到对象不自动改写；需要人工修复时另行设计。

本地上传连续失败达到 72 小时时，香橙派必须在一个事务中先写入 `PERMANENTLY_MISSING` 可靠事件及原因，再删除照片二进制；事件仍持续上报到后端确认。成功上传的照片只有在对应完成/补传事件获得 I-045 业务确认，证明后端已关联原槽位后才删除本地文件。

## I-045 后端业务确认与边缘收敛

**已确认：可靠事件只有在权威业务事务已经成功提交后才获得稳定确认；香橙派持久化确认并返回回执后，后端才停止确认重试。OneNet 的任何传输成功都不能替代这一闭环。**

### 1. 业务确认结构

```json
{
  "schemaVersion": 1,
  "confirmationUid": "62b5e5c2-ce2d-468b-b18d-6b99353239db",
  "originalEventUid": "2c496616-9858-4a58-af99-8fa0cb6ade6d",
  "originalPayloadSha256": "b7d0...64位小写十六进制",
  "outcome": "BUSINESS_APPLIED",
  "effectKind": "CREATED",
  "processedAt": "2026-07-23T05:41:10.120Z",
  "resultReferences": [
    {
      "type": "DELIVERY_ORDER",
      "key": "DO20260723..."
    }
  ],
  "errorCode": null,
  "quarantineUid": null
}
```

`outcome` 只有：

```text
BUSINESS_APPLIED
EVENT_QUARANTINED
```

`BUSINESS_APPLIED.effectKind` 只有：

```text
CREATED
UPDATED
NO_ACTION_REQUIRED
```

`NO_ACTION_REQUIRED` 表示同一事实此前已经完成，或可信迟到中间观察在更强终态后无需再改变领域状态；它仍证明后端已经安全理解并持久化该事件。`resultReferences` 只包含设备安全需要的稳定业务号，不返回租户、用户、钱包、金额、内部主键或诊断正文。

`EVENT_QUARANTINED` 只用于来源可信、事件头和 `eventUid` 可解释、但内容存在永久 Schema/摘要/身份冲突的情况；返回稳定安全错误码和可空 `quarantineUid`，不回显原始报文。无法认证、无法解析稳定事件身份或依赖暂时失败时不得伪造该终态确认。

### 2. 后端创建确认的事务边界

每个 `RELIABLE_FACT` 的业务处理事务必须一起完成：

1. 锁定并重新校验原 inbox、规范事件、作用域、稳定目标和当前领域状态；
2. 幂等创建/更新权威业务事实，或证明无需动作；
3. 把 inbox/规范事件推进为已处理终态；
4. 创建唯一 `confirmationUid` 和 `CONFIRM_EDGE_EVENT:<eventUid>` 可靠任务。

任何一步失败整体回滚，不能出现“订单已创建但永远没有确认意图”，也不能先确认设备再尝试建单。相同事件重投只唤醒/复用原处理任务和原确认，不能生成第二个订单、换袋、钱包结果或确认身份。

不同事件的最低 `BUSINESS_APPLIED` 门槛如下：

| 事件 | 必须已经提交的权威结果 | 不需要等待 |
|---|---|---|
| `DELIVERY_COMPLETE` | 整场规范物理结果、该 `sessionUid` 的唯一订单、四个整场照片槽、`negativeWeightAnomaly` 订单标志、投递后满溢检测 gate/必要采样任务、会话业务确认状态 | 订单人工审核、钱包入账、满溢最终结论、照片上传完成 |
| `CLEAN_COMPLETE` | 规范物理结果、清运员完成确认、电磁阀断电及推定关闭状态、唯一清运记录、袋交换/基准处理、四个照片槽、清运后检测 gate/必要采样任务、操作完成 | 清运审核、满溢最终结论、照片上传完成；不存在清运门门磁事实 |
| `FULLNESS_SAMPLE_COMPLETE` | 唯一样本及检测的应用/过期处置，必要容量/持续满溢事件和告警意图 | 后续人工处理或外部通知 |
| `BASELINE_MEASUREMENT_COMPLETE` | 测量终态，以及合法时的新基准和后续检测 gate/任务 | 后续检测最终结论 |
| `CONFIGURATION_PROGRESS` | 对应应用真实阶段已归并，最高期望版本规则已重算 | Web 轮询或部署激活 |
| `PHOTO_STATUS_REPORTED` | 原作业固定槽位已幂等关联为可用或永久缺失 | COS 再次查询、订单审核 |
| 命令观察/运行故障 | 对应命令、会话或运行投影已按状态机安全归并 | 由该观察触发的后续异步任务完成 |

业务处理发生暂时错误时保留原事件未确认，由 `PROCESS_INBOX` 原任务重试；自动尝试耗尽进入 `BLOCKED` 并产生聚合告警，I-036 只允许恢复原任务。不能为了停止边缘重试而把暂时错误改成 `EVENT_QUARANTINED`。

### 3. 确认投递与回执

- 后端通过 `CONFIRM_EDGE_EVENT` 控制下行发送同一确认；OneNet `code=0` 只记录技术 attempt，任务仍保持待收敛。
- 香橙派校验 `originalEventUid + originalPayloadSha256` 与本地发件箱完全一致，在一个 SQLite 事务中标记原事件已确认、保存 `confirmationUid/outcome` 并建立稳定 `BUSINESS_CONFIRMATION_RECEIPT`。后端内部 `canonicalSha256` 绑定可信外层来源，只用于中心侧冲突判断，不返回给设备冒充其可验证摘要。
- 只有该事务提交后，原事件载荷和业务上下文才进入 I-043 的清理资格；照片二进制还必须满足“槽位已关联”规则。
- 后端可信收到相同确认的回执后，把确认任务推进为 `DONE`。回执不再获得另一业务确认；香橙派在 MQTT QoS 1 PUBACK 后可清理回执正文并保留墓碑。
- 后端没有收到回执时持续按原 `confirmationUid` 重发；香橙派收到重复确认时返回同一回执，不重新处理原业务。
- `EVENT_QUARANTINED` 使香橙派停止自动重发该冲突事件，但保留事件、摘要和本地协议故障墓碑并阻止可能不安全的新作业，等待人工核查。

长时间未确认必须产生可解释的可靠任务、尝试和聚合告警；任何一端都不得按“超过若干小时”自行假定业务成功、删除原事件或换一个 `eventUid` 重发同一物理事实。

## 机器产物、测试与切换验收

实施本章时至少同时交付：

1. 权威 OneNet 物模型 JSON 和人类可读说明；两者由同一来源生成或进行字节级字段一致性检查。
2. 每种命令、事件、确认和照片授权的 JSON Schema/代码模型，以及 Java 21、Python 3.11 的规范摘要黄金样本。
3. OneNet 下行适配契约样本：`code=0`、平台拒绝、超时、响应丢失和重复发送都不误判设备/业务成功。
4. Pulsar 上行样本：合法、重复、乱序、缺号、同 ID 异摘要、同序号异事件、坏来源、不支持 Schema 和数据库暂时失败。
5. SQLite 强杀进程/断电测试：命令受理前后、门动作前后、结果和序号提交、照片改名、业务确认及回执的每个边界都不得重复物理执行或丢可靠事实。
6. COS 测试：凭证只能写精确作业前缀，不能 List/Get/Delete/跨作业写；过期刷新、重启刷新、URL 越界、同槽冲突、四图成功、部分缺图和 72 小时永久缺失均可收敛。
7. 业务确认测试：后端事务回滚不发确认，事务成功与确认任务原子存在，确认丢失/重复及回执丢失/重复均不产生第二个业务结果。
8. 一笔真实 M0 投递必须完成 OneNet 下行、设备可靠保存、四图 COS 直传、完成事件北向上报、后端建单和业务确认回执；清运、满溢和配置分别完成对应真机链路。

生产切换必须满足：

- 旧 `deliveryComplete/cleanGross/cleanTare`、旧开门服务和匿名设备写入口已禁用；
- 新物模型、后端消费者/下行适配、香橙派和 MCU/UART 兼容矩阵一致；
- 每台设备已经获得唯一有效 `deploymentCode`、正确 OneNet 身份、可信 UTC 和空的/可续接的全局事件序号；
- 后端无法生成受限 COS 临时密钥、无法消费北向消息或无法创建业务确认任务时，生产就绪检查失败或新作业保持阻断，不能退回 Fake 或占位成功。

## 本章明确不设计

- 香橙派直连中心服务器的生产 HTTP 上传；
- OneNet 物模型中的通用任意 JSON 命令/事件；
- 设备自报租户、机构、用户、单价、金额或业务终态；
- 永久 COS 密钥、桶级通配写权限、后端代传全部照片或照片 BLOB 入库；
- 用 OneNet/MQTT/Pulsar 消息 ID 生成订单、会话、事件或确认身份；
- 在未收到业务确认时按时间删除可靠事件；
- 通过新 `eventUid/commandUid` 绕过原冲突或重做物理动作；
- 当前旧物模型的生产兼容层；
- 正式 UART 二进制帧、CRC、ACK/NACK 和 MCU 精确状态机字段；这些已经由独立章节 [`I-046～I-050`](10-uart-protocol-i046-i050.md) 定义，不属于本章 OneNet/COS 线协议。

## 参考资料

- 本地 OneNet 服务调用快照：[`docs/references/设备服务调用.md`](../../references/设备服务调用.md)
- 本地 OneNet 鉴权快照：[`docs/references/安全鉴权.md`](../../references/安全鉴权.md)
- 腾讯云 COS：[临时密钥前端直传安全说明](https://cloud.tencent.com/document/product/436/40265)
- 腾讯云 COS：[临时密钥生成及策略限制](https://cloud.tencent.com/document/product/436/14048)
