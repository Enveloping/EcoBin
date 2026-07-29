# OneNet 9 服务 / 13 事件处理决策记录

> 状态：持续更新
> 建立日期：2026-07-29
> 适用范围：当前 OneNet 9 服务 / 13 事件物模型、香橙派 `fixed-frame`
> 兼容路径及其后端闭环
> 决策方式：项目负责人逐项确认；未标记“已确认”的条目不得从讨论稿推导实现要求

## 1. 已确认的共同前提

1. 当前 OneNet 控制台已经使用 9 服务 / 13 事件物模型，不再把旧
   3 服务 / 5 事件模型作为当前线上前提。
2. 当前 MCU 已冻结，无法增加协议能力，也无法取得协议没有提供的其他物理状态。
3. 当前首要目标是先让现有硬件和三端业务跑起来。为适配冻结 MCU，香橙派可以在
   `fixed-frame` 兼容边界内构造云端契约要求、但 MCU 无法实际提供的正常状态。
4. 构造的兼容状态是当前硬件约束下的正式降级语义，不应再被当作阻止业务运行的缺陷。
5. 兼容状态只解决“MCU 无法提供”的事实；命令非法、摘要错误、版本冲突、SQLite
   写入失败等香橙派能够真实判断的错误仍须返回失败。
6. 本文逐项记录当前落地决策。未来若更换 MCU 或切换 `uart-v1`，可以恢复更严格的
   物理证明，但不得反向改变当前 fixed-frame 阶段已经确认的运行语义。

## 2. 逐项确认进度

### 2.1 服务

| 序号 | OneNet 服务 | 状态 |
|---:|---|---|
| 1 | `applyConfiguration` | 已确认 |
| 2 | `startDeliverySession` | 已确认 |
| 3 | `startCleanOperation` | 已确认 |
| 4 | `endCleanBeforeUnlock` | 已确认 |
| 5 | `resumeCleanOperation` | 已确认 |
| 6 | `sampleFullness` | 已确认 |
| 7 | `measureEmptyBagBaseline` | 已确认 |
| 8 | `confirmEdgeEvent` | 已确认 |
| 9 | `providePhotoUploadGrant` | 已确认 |

### 2.2 事件

| 序号 | OneNet 事件 | 状态 |
|---:|---|---|
| 1 | `deviceCommandObserved` | 已确认 |
| 2 | `configurationProgress` | 已确认 |
| 3 | `deliveryComplete` | 已确认 |
| 4 | `cleanComplete` | 已确认 |
| 5 | `fullnessSampleComplete` | 已确认 |
| 6 | `baselineMeasurementComplete` | 已确认 |
| 7 | `deviceFaultObserved` | 已确认 |
| 8 | `deviceFaultRecovered` | 已确认 |
| 9 | `safetySensorStateChanged` | 已确认 |
| 10 | `photoStatusReported` | 已确认 |
| 11 | `photoUploadGrantRequested` | 已确认 |
| 12 | `businessConfirmationReceipt` | 已确认 |
| 13 | `deviceRuntimeSnapshot` | 已确认 |

## 3. 服务决策

### 3.1 `applyConfiguration`

状态：**已确认**

#### 3.1.1 当前阶段语义

`applyConfiguration` 在 fixed-frame 阶段表示：

> 香橙派已校验配置、可靠保存到 SQLite，并将其设为当前活动配置。

该服务不要求 MCU 实际接收配置。MCU 无法应用的字段由香橙派完整保存即可，仍然返回
下发成功。

在当前兼容语义中：

- `EDGE_SAVED` 表示完整配置已可靠写入香橙派 SQLite；
- `APPLIED` 表示该配置已经成为香橙派活动配置；
- `APPLIED` 不代表 MCU 已真实接收配置；
- `mcuCommandUid` 可以由 fixed-frame 兼容层生成；
- `mcuPayloadSha256` 保留为配置身份和完整性字段，不作为 MCU 已应用的物理证明；
- 内部状态可以保留 `mcu_configuration_projection=NOT_SUPPORTED` 供诊断，
  但它不影响本次配置成功。

#### 3.1.2 处理流程

1. 接收并解码 OneNet 同步服务调用。
2. 校验命令封套、目标、`applicationUid`、配置版本、摘要、设备配置和投口配置。
3. 校验 `target.uid == payload.applicationUid`。
4. 校验 `contentSha256` 和 `mcuPayloadSha256`，即使当前不会把 MCU 投影真正下发。
5. 在单个本地事务中保存完整配置。
6. 将该版本设置为香橙派当前活动配置。
7. 香橙派能够使用的参数从此版本读取；MCU 无法使用的参数仅持久保存。
8. 返回 OneNet 下发成功。
9. 可靠产生 `configurationProgress: EDGE_SAVED`。
10. 随后产生 `configurationProgress: APPLIED`，其中 MCU 相关身份由兼容层构造。

#### 3.1.3 幂等与版本规则

- 相同 `commandUid`、相同内容：作为重复调用返回既有成功结果，不重复产生副作用。
- 相同配置版本和相同摘要：允许按同一配置幂等处理。
- 相同配置身份但内容或摘要不同：返回冲突。
- 低于当前活动版本：返回过期配置错误，不回退活动配置。
- 新版本只有在完整 SQLite 写入成功后才能成为活动配置。

#### 3.1.4 失败边界

以下情况仍须返回失败，不能用兼容状态掩盖：

- 命令封套或字段不合法；
- 目标部署或 `applicationUid` 不匹配；
- 配置摘要不匹配；
- 配置版本过期或身份冲突；
- 投口编号、数量或配置范围不合法；
- SQLite 保存或活动版本切换失败。

“MCU 不支持配置投影”不属于当前阶段的失败条件。

#### 3.1.5 当前实现判断

当前 fixed-frame 路径在保存配置后生成兼容 `mcuCommandUid`、记录 `APPLIED`，同时把
`mcu_configuration_projection` 记为 `NOT_SUPPORTED`。这一方向符合本节确认的
运行优先决策，不再作为 `applyConfiguration` 的阻断问题。

本轮只确认处理策略并记录决策，未要求修改代码。

### 3.2 `startDeliverySession`

状态：**已确认**

#### 3.2.1 当前阶段语义

`startDeliverySession` 在 fixed-frame 阶段表示：

> 香橙派可靠保存一个云端授权的投递会话，向冻结 MCU 发送一次单价帧和启动帧，并把
> 随后收到的合法 `DD` 结果帧绑定为该会话的最终投递事实。

当前 MCU 只有一个物理投口，因此只支持 `portNo=1`。香橙派同一时刻只允许一个活动
工作槽，利用该唯一活动槽把不携带 `sessionUid` 的 `DD` 帧绑定到云端会话。

#### 3.2.2 处理流程

1. 校验命令封套、部署、目标、有效期和载荷，要求
   `target.uid == payload.sessionUid`。
2. 校验配置身份已经由 `applyConfiguration` 保存并激活。
3. 校验 `portNo=1`，并确认设备不存在其他活动投递或清运工作。
4. 在 SQLite 保存 `sessionUid`、袋身份、投口、配置快照、业务单价、等待参数、
   负重量阈值和开始命令身份，并取得唯一工作槽。
5. 配置了照片授权时先交给照片管理器；尝试拍摄投递前内外照片。
6. 无论投递前照片是否拍摄成功，都继续发送物理启动命令。照片失败必须先被记录，
   但不得释放工作槽或阻止开门。
7. 将业务单价按固定帧协议转换后，一次写出 `BB PRICE BB` 和 `AA 01 AA`。
8. MCU 没有 ACK；串口完整写出后，由兼容层构造“MCU 已接受”的正常状态。
9. 不因暂时未收到结果自动重发 `AA/BB`，避免重复执行物理开门。
10. 下一条合法 `DD PRE POST FULL DD` 绑定当前唯一活动投递：
    - `PRE`、`POST` 保存为 MCU 返回的真实重量；
    - 净重按 `POST - PRE` 计算；
    - `FULL` 保存为红外原始观测；
    - `negativeWeightAnomaly` 在当前兼容阶段固定为 `false`；
    - 收到 `DD` 即视为投递流程正常结束；
    - 门控过程使用兼容正常状态，物理门位不可观测不阻止完成。
11. 尝试拍摄投递后内外照片。投递后照片失败同样不推翻投递结果。
12. 创建唯一 `deliveryComplete` 可靠事件后释放工作槽。

OneNet 同步回执只证明命令已经由香橙派可靠接收和保存，不等待 `DD`，也不代表投递
已经完成。

#### 3.2.3 照片失败边界

项目负责人确认：

> 投递前或投递后拍照失败均不阻止发送开门命令，也不阻止投递流程完成。

具体要求为：

- 拍照失败必须保存对应照片槽的失败/缺失事实；
- COS 授权缺失、上传失败或暂时离线也不能阻止物理投递；
- 后续补传仍通过既有照片授权和照片状态机制完成；
- 照片缺失是否触发人工复核、是否影响返现，留到 `deliveryComplete`、
  `photoStatusReported` 及后端业务策略讨论时确认，不能从本条决定自行推导。

#### 3.2.4 幂等、迟到结果与重启

- 相同 `commandUid`、相同内容只返回既有受理结果，绝不再次发送 `AA/BB`。
- 同一设备同时只能存在一个活动工作槽，避免无作业身份的 `DD` 错绑。
- 已无活动投递时收到迟到或重复 `DD`，直接忽略，不新建投递事实。
- 香橙派重启或等待 `DD` 超时后不重放物理启动命令；本地会话置失败并释放工作槽。
- fixed-frame 没有 MCU 状态查询和作业恢复能力，不尝试猜测 MCU 是否仍在执行。

#### 3.2.5 固定帧兼容值

- MCU 显示单价继续按既有协议向下一位小数转换并在单字节范围内饱和；
- 后端结算继续使用命令中保存的原始 `unitPriceTenThousandths`，不使用 MCU 显示值；
- `negativeWeightAnomaly=false`；
- 收到 `DD` 后使用 `completionReason=USER_ENDED`；
- 关闭命令使用兼容成功状态；协议无法提供的物理门位保持不可观测，但不阻塞建单。

#### 3.2.6 当前实现判断与保留任务

当前代码已经实现单投口工作槽、投递上下文持久化、`BB+AA` 单次发送、`DD` 重量解析、
净重计算、投递完成事件和工作槽释放，主流程符合本节决策。

仍需在后续实施中处理：

1. 当前投递前照片保存失败会返回 `PHOTO_CAPTURE_NOT_PERSISTED`、释放工作槽并停止开门，
   与本轮确认结果冲突，必须改为记录照片失败后继续发送 `BB+AA`。
2. 后端当前尚未创建和下发 `startDeliverySession`。
3. 香橙派需要补齐 `target.uid == payload.sessionUid` 的语义校验。
4. 受理后若能证明 UART 尚未写出即失败，上报
   `deviceCommandObserved: PRE_START_FAILED`；UART 可能已经写出、等待 `DD`
   超时或香橙派重启时上报 `FAILED`，具体规则见 4.1。
5. 照片缺失对人工复核和返现的影响，随相关事件及后端业务策略单独确认。

本轮只确认处理策略并记录决策，未修改运行代码。

### 3.3 `startCleanOperation`

状态：**已确认**

#### 3.3.1 当前阶段语义

`startCleanOperation` 在 fixed-frame 阶段表示：

> 香橙派可靠保存清运操作、旧袋皮重和新袋身份，向冻结 MCU 发送一次 `EE 01 EE`；
> MCU 独立完成屏幕、按钮、称重、电磁阀和现场确认流程，香橙派把随后收到的合法
> `EF` 结果帧绑定为该操作的最终清运事实。

当前 MCU 只有一个物理投口，因此只支持 `portNo=1`。香橙派同一时刻只允许一个活动
工作槽，利用该唯一活动槽把不携带 `operationUid` 的 `EF` 帧绑定到云端清运操作。

#### 3.3.2 处理流程

1. 校验命令封套、部署、目标、有效期和载荷，要求
   `target.uid == payload.operationUid`。
2. 校验配置身份已经由 `applyConfiguration` 保存并激活。
3. 校验 `portNo=1`，并确认设备不存在其他活动投递或清运工作。
4. 在 SQLite 保存 `operationUid`、旧袋身份、旧袋皮重、新袋身份、配置快照、
   操作期限和开始命令身份，并取得唯一工作槽。
5. 配置了照片授权时先交给照片管理器；尝试拍摄首次开门前的内外照片。
6. 无论清运前照片是否拍摄成功，都继续发送 `EE 01 EE`。照片失败必须先被记录，
   但不得释放工作槽或阻止清运开始。
7. MCU 没有 ACK；串口完整写出后，由兼容层构造“MCU 已接受”的正常状态。
8. 不因暂时未收到结果自动重发 `EE`，避免重复解锁。
9. MCU 独立完成以下冻结流程：
   - 保存清运前总重量；
   - 为清运门电磁阀通电，并在 3～5 秒后自动断电；
   - 通过屏幕和按钮引导换袋；
   - 等待清运员关闭清运门并确认完成；
   - 获取稳定的清运后总重量和红外原始观测；
   - 只发送一次 `EF PRE POST FULL EF`。
10. 香橙派收到合法 `EF` 后计算清运结果、尝试拍摄最终关门后的内外照片、创建唯一
    `cleanComplete` 可靠事件并保存新袋皮重，随后释放工作槽。

OneNet 同步回执只证明命令已经由香橙派可靠接收和保存，不等待 `EF`，也不代表清运
已经完成。

#### 3.3.3 `EF` 字段与净重计算

项目负责人确认：

```text
preUnlockTotalWeightGrams = EF.PRE
newBaselineWeightGrams    = EF.POST
removedNetWeightGrams     = EF.PRE - oldBaselineWeightGrams
```

具体语义为：

- `PRE` 是 MCU 收到 `EE` 后、首次解锁前保存的真实总重量；
- `POST` 是换入新空袋、清运员关门并确认后取得的稳定总重量；
- `POST` 成为 `newBagUid` 的新皮重；
- 垃圾净重必须使用旧袋皮重计算，禁止使用 `PRE - POST`；
- 旧皮重缺失时不能退化为 `PRE - POST`，净重保持不可计算，但仍可保存新的
  `POST` 皮重并完成清运；
- 正、零、负净重均保留真实计算值，不强制归零；
- `FULL` 只保存为清运完成后的红外原始观测。

#### 3.3.4 fixed-frame 兼容状态

根据冻结协议，`EF` 只会在继电器已经自动断电、清运员完成换袋、关闭清运门并在 MCU
屏幕确认后发送。因此收到合法 `EF` 时允许构造：

- `cleanerCompletionConfirmed=true`；
- `lockPowerState=DEENERGIZED`；
- `physicalDoorStateBasis=CLEANER_CONFIRMATION`；
- `cleanerPhysicalCloseConfirmed=true`；
- `solenoidHealth=UNKNOWN`；
- `cleanActionSequence=1`。

这些兼容值用于让现有硬件完成云端契约，不要求 MCU 增加额外状态帧。

#### 3.3.5 照片失败边界

项目负责人确认：

> 清运前或清运后拍照失败均不阻止发送 `EE`，也不阻止收到 `EF` 后完成清运。

照片失败必须保存对应槽位的失败/缺失事实；COS 授权缺失、上传失败或暂时离线也不能
阻止物理清运。照片缺失对后端人工复核等业务结果的影响，留到 `cleanComplete` 和照片
相关事件讨论时确认。

#### 3.3.6 幂等、超时与重启

- 相同 `commandUid`、相同内容只返回既有受理结果，绝不再次发送 `EE`。
- 同一设备同时只能存在一个活动工作槽。
- 超过操作期限仍未收到 `EF` 时，本地操作置失败并释放工作槽，不重发 `EE`。
- 香橙派重启后不查询或恢复 MCU 清运，不重发旧 `EE`；旧操作置失败并释放工作槽。
- 已无活动清运操作时收到迟到或重复 `EF`，直接忽略，不创建清运事实。

#### 3.3.7 当前实现判断与保留任务

当前代码已经实现单投口工作槽、清运上下文持久化、`EE` 单次发送、`EF` 解析、
兼容完成状态、清运完成事件、新皮重保存和工作槽释放，主流程基本成立。

仍需在后续实施中处理：

1. 当前代码使用 `EF.PRE - EF.POST` 计算 `removedNetWeightGrams`，必须改为
   `EF.PRE - oldBaselineWeightGrams`。
2. 当前清运前照片保存失败会返回 `PHOTO_CAPTURE_NOT_PERSISTED`、释放工作槽并停止发送
   `EE`，必须改为记录照片失败后继续清运。
3. 后端当前尚未创建和下发 `startCleanOperation`。
4. 香橙派需要补齐 `target.uid == payload.operationUid` 的语义校验。
5. 受理后若能证明 UART 尚未写出即失败，上报
   `deviceCommandObserved: PRE_START_FAILED`；UART 可能已经写出、等待 `EF`
   超时或香橙派重启时上报 `FAILED`，具体规则见 4.1。
6. 照片缺失对后端业务结果的影响，随相关事件单独确认。

本轮只确认处理策略并记录决策，未修改运行代码。

### 3.4 `endCleanBeforeUnlock`

状态：**已确认**

#### 3.4.1 当前阶段语义

fixed-frame MCU 没有取消清运命令，也不能向香橙派证明首次解锁尚未发生。项目负责人
确认本服务不构造兼容成功状态：

> fixed-frame 模式收到 `endCleanBeforeUnlock` 时直接返回拒绝，不执行命令。

#### 3.4.2 拒绝处理

1. 香橙派完成 OneNet 服务标识和命令封套的最小解码，以取得合法 `commandUid`。
2. 在 fixed-frame 能力检查中识别该服务不受支持。
3. 同步回复：

   ```text
   receiptState = REJECTED
   errorCode = MCU_FEATURE_NOT_SUPPORTED
   ```

4. 不把命令放入异步执行队列。
5. 不向 MCU 发送任何 UART 帧。
6. 不取得、释放或修改工作槽。
7. 不改变当前活动 `startCleanOperation` 的状态。
8. 不创建 `cleanComplete`，也不伪造“从未解锁”或“取消成功”状态。
9. 相同命令重复调用时继续返回相同拒绝结果，不产生副作用。

命令格式错误、部署或目标不匹配时仍返回相应校验错误，不能统一伪装成
`MCU_FEATURE_NOT_SUPPORTED`。

#### 3.4.3 当前实现判断与保留任务

当前 `WorkManager.end_clean_before_unlock_command()` 已在 fixed-frame 模式返回
`MCU_FEATURE_NOT_SUPPORTED`，且不会发送 UART 帧或修改工作槽，这一执行边界正确。

但当前 MQTT 服务入口会先把命令可靠保存并同步返回 `ACCEPTED`，随后异步执行器才得到
`MCU_FEATURE_NOT_SUPPORTED`。这与“直接返回 `REJECTED`”的确认结果不一致。

后续实施需要把 fixed-frame 能力检查前移到同步服务受理阶段，确保：

- OneNet 调用方直接收到 `REJECTED / MCU_FEATURE_NOT_SUPPORTED`；
- 该命令不进入普通待执行队列；
- 为该合法拒绝可靠产生 `deviceCommandObserved: REJECTED`，错误码使用
  `MCU_FEATURE_NOT_SUPPORTED`，具体规则见 4.1。

后端当前也尚未创建和下发 `endCleanBeforeUnlock`，后续接入时必须把设备同步拒绝保存为
命令终态，不能持续重试不受支持的能力。

本轮只确认处理策略并记录决策，未修改运行代码。

### 3.5 `resumeCleanOperation`

状态：**已确认**

#### 3.5.1 当前阶段语义

`resumeCleanOperation` 用于超时或重启后由原清运员恢复同一个 `operationUid`，不等同于
正常清运流程内由 MCU 屏幕控制的再次开门。

fixed-frame MCU 没有作业身份、状态查询、恢复代际，也没有恢复命令帧。结合已经确认的
“超时或香橙派重启后，旧清运操作置失败并释放工作槽”规则，当前阶段不存在可以安全
恢复的本地清运上下文。

项目负责人确认：

> fixed-frame 模式收到 `resumeCleanOperation` 时直接返回拒绝，不执行命令，也不构造
> 恢复成功状态。

#### 3.5.2 拒绝处理

1. 香橙派完成 OneNet 服务标识和命令封套的最小解码，以取得合法 `commandUid`。
2. 在 fixed-frame 能力检查中识别该服务不受支持。
3. 同步回复：

   ```text
   receiptState = REJECTED
   errorCode = MCU_FEATURE_NOT_SUPPORTED
   ```

4. 不把命令放入异步执行队列。
5. 不保存或使用命令附带的 COS 临时授权。
6. 不创建、取得、释放或修改工作槽。
7. 不修改原 `startCleanOperation` 或任何历史清运操作。
8. 不发送 `EE`，也不发送任何其他 UART 帧。
9. 不更新 `recoveryGeneration`，不构造恢复状态或 MCU 身份。
10. 相同命令重复调用时继续返回相同拒绝结果，不产生副作用。

命令格式错误、部署或目标不匹配时仍返回相应校验错误，不能统一伪装成
`MCU_FEATURE_NOT_SUPPORTED`。

#### 3.5.3 后端处理

后端收到 `REJECTED / MCU_FEATURE_NOT_SUPPORTED` 后应把该恢复命令置为永久失败并停止
重试。若业务仍需要继续清运，只能按当前业务规则重新创建新的清运操作，不能把恢复命令
改写成新的 `startCleanOperation`，也不能复用旧 `commandUid`。

#### 3.5.4 当前实现判断与保留任务

当前 `WorkManager.resume_clean_command()` 已在 fixed-frame 模式返回
`MCU_FEATURE_NOT_SUPPORTED`，且不会修改工作槽或发送 UART，这一执行边界正确。

但当前 MQTT 服务入口会先把命令保存并同步返回 `ACCEPTED`，随后异步执行器才得到
`MCU_FEATURE_NOT_SUPPORTED`。后续实施需要把 fixed-frame 能力检查前移到同步服务
受理阶段，确保 OneNet 调用方直接收到永久拒绝，命令不进入普通待执行队列。

同时为该合法拒绝可靠产生 `deviceCommandObserved: REJECTED`，错误码使用
`MCU_FEATURE_NOT_SUPPORTED`，具体规则见 4.1。
后端当前也尚未创建和下发 `resumeCleanOperation`。

本轮只确认处理策略并记录决策，未修改运行代码。

### 3.6 `sampleFullness`

状态：**已确认**

#### 3.6.1 当前阶段语义

fixed-frame MCU 没有独立满溢采样命令，只会在 `DD` 或 `EF` 结果帧中附带流程结束后的
总重量和红外原始位。项目负责人确认三个采样角色全部使用香橙派保存的最近一次流程结束
总重量，不因无法执行新的 MCU 采样而拒绝：

- `INITIAL`；
- `CONFIRMATION`；
- `MANUAL_RECHECK`。

该服务完全在香橙派本地完成，不向 MCU 发送任何 UART 帧，也不占用物理工作槽。

#### 3.6.2 最近总重量

香橙派在 SQLite 中持久保存当前最近总重量：

1. 收到投递结果 `DD` 后，以 `DD.POST` 更新最近总重量；
2. 收到清运结果 `EF` 后，以 `EF.POST` 更新最近总重量；
3. 因此清运完成后的新空袋重量会覆盖清运前的旧投递重量；
4. 香橙派重启后继续使用 SQLite 中的该值；
5. 设备首次启动且从未收到合法 `DD/EF` 时，使用兼容值 `0` 克。

三个 `sampleRole` 和三个 `triggerType` 都读取调用时的同一个最近总重量，不要求重新
采样，也不要求最近结果必须来自与 `triggerType` 同名的流程。

#### 3.6.3 满溢度公式

项目负责人确认：

```text
fullnessPercent =
    latestTotalWeightGrams
    / configuredFullWeightGrams
    × 100%
```

其中：

- `latestTotalWeightGrams` 为最近的 `DD.POST`、`EF.POST`，或无历史时的 `0`；
- `configuredFullWeightGrams` 为本次命令下发的满溢重量；
- 不扣除 `currentBaselineWeightGrams`；
- 不因 `fullnessMode` 不同改变该百分比公式；
- 允许结果超过 100%；
- 无历史重量时结果为 0%；
- `currentBaselineWeightGrams` 和 `fullnessMode` 仍可作为命令快照保存，但不参与当前
  fixed-frame 满溢度计算或 `FULL/NOT_FULL` 判定。

fixed-frame 最终状态同样只按重量百分比判断：

```text
fullnessPercent >= 100%  -> FULL
fullnessPercent < 100%   -> NOT_FULL
```

`DD/EF.FULL` 只作为红外原始辅助观测保存，不参与最终状态。这样不会产生页面显示重量
百分比未满、但红外原始位又把同一结果判满的双重业务口径。

当前 `fullnessSampleComplete` 事件没有直接的 `fullnessPercent` 字段。为避免修改已经
导入 OneNet 控制台的 9 服务 / 13 事件模型，香橙派上报最近总重量和冻结配置，后端按
上述公式计算并对外返回满溢度。

#### 3.6.4 事件兼容值

每次调用都为对应的 `(detectionUid, sampleRole)` 创建唯一
`fullnessSampleComplete`：

- `fullnessSampleBasis=NOT_SAMPLED`；
- 有历史 `DD/EF` 时，`totalWeightMeasurement` 使用该帧真实 `POST` 和原 MCU 兼容身份；
- 有历史 `DD/EF` 时，保留该帧真实红外值 `CLEAR/BLOCKED`；
- 有历史值时 `requestedSampleCount=1`、`validSampleCount=1`；
- 无历史值时构造 0 克正常测量和兼容 MCU 身份；
- 无历史值时红外值构造为 `CLEAR`；
- 无历史值时 `requestedSampleCount=0`、`validSampleCount=0`；
- `representativeDistanceMm=null`；
- 嵌套重量测量的兼容 `sampleCount=1`，表示事件提供一个可用重量结果，不表示重新驱动
  MCU 采样；
- 事件和测量身份使用持久化 UUIDv4。

红外值在当前决策中只作为附带原始观测，不参与已经确认的重量百分比公式或最终
`FULL/NOT_FULL` 判定。无历史的 `0 克 + CLEAR` 对三个角色都作为正常兼容结果处理，
包括允许 `CONFIRMATION` 或 `MANUAL_RECHECK` 得出 `NOT_FULL`。

#### 3.6.5 受理与幂等

1. 校验命令封套、部署、`detectionUid`、`portNo=1`、配置身份和载荷范围。
2. 校验 `target.uid == payload.detectionUid`。
3. 命令可靠保存后同步返回 `ACCEPTED`。
4. 香橙派读取最近总重量，在本地立即创建可靠结果事件。
5. 相同 `commandUid` 和相同内容只返回既有受理结果。
6. 同一 `detectionUid` 可以先有 `INITIAL`、再有 `CONFIRMATION`；每个
   `(detectionUid, sampleRole)` 最多一个不可变结果。
7. 同一角色的技术重试复用原事件身份、冻结观测和内容，不读取重试时的新缓存。
8. 由于没有 MCU 动作，不存在 UART 超时、物理重发或设备工作槽恢复。

#### 3.6.6 当前实现判断与保留任务

当前代码已经能够复用 SQLite 中最近一次 `DD/EF` 的 `POST` 重量和红外原始位，并以
`NOT_SAMPLED` 创建 `fullnessSampleComplete`，这一基础路径可以保留。

仍需在后续实施中处理：

1. 当前只允许 `sampleRole=INITIAL`，必须改为
   `INITIAL/CONFIRMATION/MANUAL_RECHECK` 全部本地完成。
2. 当前没有历史 `DD/EF` 时返回 `MCU_FEATURE_NOT_SUPPORTED`，必须改为使用 0 克、
   0% 和 `CLEAR` 兼容值。
3. 后端必须按 `latestTotalWeightGrams / configuredFullWeightGrams` 计算满溢度，
   不再扣除当前袋基准，并只按该百分比判定 `FULL/NOT_FULL`。
4. 后端当前尚未创建和下发 `sampleFullness`，也尚未消费
   `fullnessSampleComplete`。
5. 香橙派需要补齐 `target.uid == payload.detectionUid` 的语义校验。
6. 当前本地结果错误占用全局工作槽，fixed-frame 启动还会主动清空最近观测缓存。
7. 当前尚未按 `(detectionUid, sampleRole)` 冻结来源观测和稳定事件身份。

本轮只确认处理策略并记录决策，未修改运行代码。

### 3.7 `measureEmptyBagBaseline`

状态：**已确认**

#### 3.7.1 当前阶段语义

fixed-frame MCU 没有独立称重命令，香橙派无法在服务调用发生时重新取得当前重量。
项目负责人确认该服务仍须优先跑通：

> `emptyBagConfirmed=true` 表示现场已经确认当前袋为空；香橙派从本地已有重量中选择
> 兼容皮重，保存为该 `bagUid` 的当前皮重并返回成功。

该服务完全在香橙派本地完成，不向 MCU 发送 UART 帧，也不占用物理工作槽。所返回的
重量不是服务调用时的新采样值，而是 fixed-frame 能力边界内的兼容值。

#### 3.7.2 皮重选择顺序

香橙派按以下顺序确定本次皮重：

1. 优先使用当前 `bagUid` 已保存、由最近一次清运结果 `EF.POST` 建立的皮重；
2. 当前袋没有对应皮重时，使用 SQLite 中最近一次合法流程结果的总重量，即最近的
   `DD.POST` 或 `EF.POST`；
3. 设备没有任何合法 `DD/EF` 历史重量时，使用兼容皮重 `0` 克。

第二级回退可能把含有垃圾的 `DD.POST` 当作空袋皮重，进而影响以后按
`PRE - oldBaselineWeightGrams` 计算的清运净重。项目负责人已知悉该风险，并确认当前
阶段以跑通业务为优先，仍采用此回退规则。

选中的重量必须保存到 SQLite，成为命令中 `bagUid` 的当前皮重；后续清运不得只依赖
内存中的临时结果。香橙派同时保留该皮重来自“同袋 `EF.POST`”“最近流程结果”或
“无历史 0 克兼容值”的内部来源信息，便于诊断。

#### 3.7.3 处理流程

1. 校验命令封套、部署、`measurementUid`、`portNo=1`、`bagUid`、配置身份及载荷范围。
2. 校验 `target.uid == payload.measurementUid`。
3. `emptyBagConfirmed` 必须为 `true`；否则作为非法命令拒绝，不能伪造现场确认。
4. 命令可靠写入后，同步返回 `ACCEPTED`。
5. 按本节顺序从 SQLite 选择皮重；没有历史值时选择 0 克，不返回
   `MCU_FEATURE_NOT_SUPPORTED`。
6. 将选中重量可靠保存为该 `bagUid` 的当前皮重。
7. 立即创建唯一的 `baselineMeasurementComplete` 可靠事件并进入本地 outbox。
8. 事件可靠落库后将命令标记完成；后续 MQTT 重发不重新选择皮重。

整个流程不等待 MCU ACK、称重结果或超时，也不触发设备忙状态。

#### 3.7.4 事件兼容值

`baselineMeasurementComplete` 使用本次命令的：

- `measurementUid`；
- `portNo=1`；
- `bagUid`；
- `emptyBagConfirmed=true`；
- 冻结配置快照。

`totalWeightMeasurement` 将选中的克数表达为成功、可用的兼容测量：

- 有历史 `DD/EF` 时，重量取对应 `POST`，并在可能范围内保留来源 MCU 观测身份；
- 使用已保存的同袋皮重时，返回该皮重原有的重量和来源信息；
- 无历史时返回 0 克，并由香橙派构造满足云端契约的正常测量身份；
- 嵌套测量固定使用正常稳定形状和 `sampleCount=1`；
- 顶层 `measurementUid`、嵌套测量身份和 `eventUid` 是三个不同、可靠保存的 UUIDv4；
- 以上三种情况都不得因为 MCU 没有执行本次独立采样而把服务或事件标记为失败。

兼容来源必须保留在香橙派内部诊断数据中，不能把复用的历史重量误记为调用时发生的
真实物理采样。后端也应把结果来源记录为 fixed-frame 兼容建立，而不是对运营审计宣称
发生了一次新的 MCU 实测。

#### 3.7.5 幂等、重启与失败边界

- 相同 `commandUid`、相同内容：返回既有受理和完成结果，不重复更新皮重。
- 相同 `measurementUid`：只能对应一个确定的皮重和一个完成事件；技术重试复用原结果。
- 以新的 `commandUid` 重复请求同一 `measurementUid`，也不能重新选择皮重或建立第二
  结果。
- 香橙派重启后从 SQLite 恢复已选皮重、命令终态和待上报事件，不重新选择最新重量。
- 没有历史重量不是失败条件，按 0 克成功完成。
- MCU 不支持独立皮重测量不是失败条件。
- 命令非法、目标或配置身份不匹配、`emptyBagConfirmed` 不为 `true`、重量越界、
  命令身份冲突、SQLite 保存失败或事件可靠落库失败仍须返回或记录真实失败。

#### 3.7.6 当前实现判断与保留任务

当前 fixed-frame 路径会直接返回 `MCU_FEATURE_NOT_SUPPORTED`，不符合本节确认的
运行优先策略。后续实施需要：

1. 将 fixed-frame 路径改为本地完成，不创建物理工作槽，也不发送
   `MEASURE_BASELINE`；
2. 增加按 `bagUid` 保存和读取当前皮重及来源信息的 SQLite 能力；
3. 复用最近 `DD.POST/EF.POST`，并补齐无历史时的 0 克正常兼容测量；
4. 在同一可靠流程中保存皮重、完成命令并创建
   `baselineMeasurementComplete`；
5. 补齐 `target.uid == payload.measurementUid`、幂等、重启恢复和回退顺序测试。
6. 后端需要增加 fixed-frame 兼容皮重来源，不能把历史值或 0 克兼容值伪记成真实
   `MANUAL_REMEASUREMENT`。

非 fixed-frame 的 `uart-v1` 独立测量路径可以继续使用物理工作槽、
`MEASURE_BASELINE` 和 `BASELINE_MEASUREMENT_RESULT`，本决策不要求删除该能力。

本轮只确认处理策略并记录决策，未修改运行代码。

### 3.8 `confirmEdgeEvent`

状态：**已确认**

#### 3.8.1 当前阶段语义

`confirmEdgeEvent` 与 MCU 无关，是后端和香橙派之间用于终结可靠事件的业务确认服务：

> 后端已经安全处理或永久隔离某个 `RELIABLE_FACT` 后，将稳定确认下发给香橙派；
> 香橙派可靠保存确认、停止原事件重传，并返回一条稳定控制回执。

OneNet 服务调用成功、MQTT PUBACK 或后端收到原事件都不能代替该业务确认。后端只能在
原事件对应的权威业务事务已经成功提交，并且确认意图与业务结果一同可靠保存后下发。

该服务不发送 UART，不读取或构造 MCU 状态，也不占用物理工作槽。

#### 3.8.2 香橙派校验

香橙派必须先完整校验命令封套和载荷，至少包括：

1. Schema 版本、`commandUid`、部署身份、签发时间、过期时间和命令载荷摘要合法；
2. `target.type=EDGE_EVENT`；
3. `target.uid == payload.originalEventUid`；
4. `originalEventUid` 在本地发件箱中存在，且原事件属于 `RELIABLE_FACT`；
5. `originalPayloadSha256` 与本地原事件的稳定载荷摘要完全一致；
6. `confirmationUid`、`processedAt`、`outcome` 及各条件字段满足物模型约束。

两种结果的条件必须严格区分：

- `BUSINESS_APPLIED`：`effectKind` 为
  `CREATED/UPDATED/NO_ACTION_REQUIRED` 之一，`errorCode` 和
  `quarantineUid` 为空；
- `EVENT_QUARANTINED`：`effectKind` 为空、`resultReferences` 为空，
  并携带稳定 `errorCode`；`quarantineUid` 可空。

原事件不存在、摘要不一致、目标不一致或条件字段冲突时同步返回 `REJECTED`，不得停止
原事件重传，也不得创建成功回执。

#### 3.8.3 原子处理流程

合法确认必须在一个 SQLite 事务中完成：

1. 保存完整确认及其稳定身份；
2. 将原事件标记为已确认并停止自动重传；
3. 创建唯一的 `businessConfirmationReceipt`；
4. 可靠提交原事件状态、确认记录和回执发件箱记录。

只有事务提交成功后才同步返回 `ACCEPTED`。回执进入正常可靠发送流程，但
`businessConfirmationReceipt` 是 `CONTROL_RECEIPT`，后端不得再对它产生另一层
`confirmEdgeEvent`。

`BUSINESS_APPLIED` 使原事件进入正常清理资格；照片二进制等关联资源仍须满足各自的
额外清理条件。

#### 3.8.4 幂等与回执重发

- 后端重试必须复用原 `commandUid`、`confirmationUid` 和完整确认内容。
- 完全相同的重复确认返回 `DUPLICATE_ACCEPTED`。
- 重复确认必须重新唤醒并发送第一次创建的同一条
  `businessConfirmationReceipt`，复用原 `eventUid`、`edgeEventSequence` 和载荷，
  不能创建第二条回执。
- 相同 `confirmationUid` 但内容不同，返回幂等冲突。
- 同一个原事件只能收敛到一个稳定确认身份；不能通过更换
  `confirmationUid` 改写已经保存的确认结果。
- 香橙派重启后从 SQLite 恢复确认和回执；不能重新处理原业务，也不能因回执此前获得
  MQTT PUBACK 就拒绝后端要求的同一回执重发。

后端只有在可信收到并校验同一回执后，才能把确认任务置为完成。OneNet 下发返回成功
只记录一次技术发送结果，不能提前结束确认任务。

#### 3.8.5 `EVENT_QUARANTINED` 的运行优先规则

项目负责人确认：

> `EVENT_QUARANTINED` 只隔离对应的原事件并记录诊断，不锁定整台设备，也不阻止后续
> 新投递或新清运。

香橙派收到合法隔离确认后：

- 停止重传该原事件；
- 保存原事件身份、摘要、隔离结果、错误码和可选 `quarantineUid`；
- 创建并发送正常业务确认回执；
- 保留可供人工排查的隔离记录或墓碑；
- 不占用工作槽，不建立全局安全锁，不因该历史事件拒绝后续作业。

该规则是当前“先跑起来”阶段对原设计中保守阻断语义的明确覆盖。它不允许香橙派自行
把暂时发送失败或未知错误改成 `EVENT_QUARANTINED`；隔离结果只能来自通过完整校验的
后端确认。

#### 3.8.6 当前实现判断与保留任务

香橙派当前已有首次接收确认、比对原事件摘要、在 SQLite 中标记原事件并创建
`businessConfirmationReceipt` 的基础事务，但尚不完整：

1. `CONFIRM_EDGE_EVENT` 当前绕过普通命令的完整封套校验；
2. 尚未完整校验 target、期限、载荷摘要及两种 outcome 的条件字段；
3. 相同 `confirmationUid` 的异内容当前会被误当作普通重复；
4. 重复确认不会重新发送第一次创建的同一回执；
5. `EVENT_QUARANTINED` 的诊断记录和“不锁整机”规则需要显式实现和测试；
6. 回执及其他可靠事件的 `SENDING` 重启恢复路径仍需补齐。

后端当前既不会下发 `confirmEdgeEvent`，也不会消费
`businessConfirmationReceipt`。后续实施需要：

1. 在每个可靠事件的权威业务事务中原子创建稳定确认意图；
2. 增加独立的协议控制任务路径，不能把确认伪装成普通设备业务命令；
3. 重试时始终复用原确认身份和内容，且不能用 OneNet 调用成功代替回执；
4. 注册并严格校验 `businessConfirmationReceipt`；
5. 回执与冻结确认完全匹配后才结束任务，且不产生递归确认；
6. 至少先以 `configurationProgress` 打通一条
   “业务归并 → 确认下发 → 香橙派落库 → 回执上报 → 后端任务完成”的纵向链路。

本轮只确认处理策略并记录决策，未修改运行代码。

### 3.9 `providePhotoUploadGrant`

状态：**已确认**

#### 3.9.1 当前阶段语义

`providePhotoUploadGrant` 是后端响应香橙派照片补授权请求的协议控制服务，与 MCU 无关：

> 香橙派确认授权对应当前仍有效的照片请求，接收临时 COS 凭证并唤醒异步上传；
> 服务受理成功不代表照片已经上传成功。

该服务不发送 UART、不占用物理工作槽，也不改变投递或清运状态。照片拍摄、授权或上传
失败继续遵循已确认的运行优先规则：记录缺失或等待补传，但不阻止开门、流程完成和结果
上报。

#### 3.9.2 请求匹配与槽位

香橙派在返回成功前必须校验：

1. 命令封套、部署、期限、稳定载荷摘要及 COS 授权结构合法；
2. `target.type=PHOTO_GRANT_REQUEST`；
3. `target.uid == payload.grantRequestEventUid`；
4. `grantRequestEventUid` 对应香橙派当前仍待处理的
   `photoUploadGrantRequested`；
5. `workType`、`workUid` 与该请求及本地照片记录完全一致；
6. `authorizedSlots` 是对应作业类型的固定四槽集合；
7. COS bucket、region、`baseUrl`、`keyPrefix` 和授权期限与当前部署及作业身份一致。

固定槽位为：

- 投递：`BEFORE_INNER`、`BEFORE_OUTER`、`AFTER_INNER`、`AFTER_OUTER`；
- 清运：`FIRST_OPEN_INNER`、`FIRST_OPEN_OUTER`、`FINAL_CLOSE_INNER`、
  `FINAL_CLOSE_OUTER`。

服务可以携带完整四槽授权，但香橙派只上传本地仍处于待处理状态的照片，不覆盖已经完成
或永久缺失的槽位。

未知请求、已失效请求、重启前的旧请求、作业身份不匹配或授权已经过期时直接返回
`REJECTED`。拒绝补授权只保留照片待处理状态，不得使原投递或清运失败。

#### 3.9.3 凭证保存边界与受理时点

临时 COS 凭证是执行期数据：

- `tmpSecretId`、`tmpSecretKey` 和 `sessionTokenParts` 只保存在香橙派进程内存；
- 不写入 SQLite、普通日志、稳定命令摘要或业务事件；
- SQLite 只保存去除 `cosGrant` 后的稳定命令身份、请求身份和执行状态；
- 香橙派重启后不尝试恢复或复用旧凭证。

香橙派按以下顺序受理：

1. 完成请求和授权校验；
2. 可靠保存脱敏后的稳定命令信息；
3. 将本次临时凭证安装到对应作业的内存上传器；
4. 唤醒照片上传任务；
5. 只有上述步骤成功后才同步返回 `ACCEPTED`。

SQLite 与内存不能形成同一个物理事务；若进程在可靠保存命令后、安装或使用凭证前退出，
重启恢复统一为待上传状态并生成新的 `photoUploadGrantRequested`，不得把旧命令的
脱敏记录当成凭证仍然可用。

#### 3.9.4 异步上传与失败处理

返回 `ACCEPTED` 后，照片在后台异步上传：

- 上传成功后按照片事件规则上报 `photoStatusReported: AVAILABLE`；
- 普通网络错误进入照片自己的退避重试；
- 授权过期、COS 鉴权或签名错误立即使当前内存授权失效；
- 仍有待传照片时创建新的 `photoUploadGrantRequested`，不复用已经失效的请求身份；
- 最终无法上传的照片按照片事件规则收敛为缺失，不回滚或阻塞投递、清运及其完成事件；
- 后端暂时无法生成真实临时凭证时不伪造生产凭证，只保留补授权任务重试。

#### 3.9.5 幂等与重启

`cosGrant` 不参与稳定命令摘要，因此同一稳定补授权命令可以在技术重试时携带新签发的
短期凭证：

- 相同 `commandUid`、相同稳定内容、请求仍有效：返回
  `DUPLICATE_ACCEPTED`，并把新的有效凭证重新注入或替换内存中的旧凭证；
- 相同 `commandUid`、稳定内容不同：返回幂等冲突；
- 请求已经失效或重启后已经生成新请求：即使旧命令身份相同，也拒绝迟到授权；
- 重启后的待传照片使用新的请求事件和新的授权，不依赖重启前的内存状态；
- 重复受理不得创建新的投递/清运作业，也不得产生 MCU 动作。

#### 3.9.6 当前阶段安全延期

项目负责人确认当前首要目标是跑通链路，暂时不把 STS 最小权限加固作为实施阻断项。
因此当前后端签发策略即使仍包含桶级通配范围、额外上传动作或 multipart 权限，也不阻止
本服务进入本轮实现和联调。

精确限制为当前作业前缀且只允许 `PutObject` 的策略作为后续安全任务保留。本延期不改变
本节已经确认的运行边界：临时密钥仍然只驻留内存，不写入版本库、SQLite、稳定消息或
日志，也不能改用永久 COS 密钥。

#### 3.9.7 当前实现判断与保留任务

香橙派当前已经具备大部分基础能力：

- 能校验授权环境、规范 HTTPS 地址、作业前缀和固定槽位；
- 稳定命令落库时会把 `cosGrant` 清空；
- 临时密钥只放在内存；
- 能异步上传、申请补授权并上报照片状态；
- 已有测试检查 SQLite/WAL 中不包含临时密钥字节。

后续实施仍需处理：

1. 当前在核对请求仍有效、并把凭证真正安装到上传器之前就同步返回成功，必须调整受理
   时点；
2. 只有当前请求仍有效时，重复命令才能返回
   `DUPLICATE_ACCEPTED` 并重新注入凭证；
3. 已完成、失败或重启换代后的旧请求不得出现“回复成功但实际丢弃凭证”；
4. COS 鉴权和签名错误应立即淘汰坏凭证并创建新请求，而不是等自然过期；
5. 照片授权控制命令的重启恢复不能套用物理命令恢复规则；
6. 已确认的“拍照失败不阻止开门”尚未完全落实，当前投递和清运的首次拍照失败路径仍
   可能阻止启动，必须一并修正。

后端当前尚未建立 `PROVIDE_PHOTO_UPLOAD_GRANT:<grantRequestEventUid>` 控制任务和
OneNet 下发路径。后续需消费 `photoUploadGrantRequested`、恢复可信作业身份、在实际
发送时签发短期凭证，并在收到香橙派同步受理结果后收敛本次授权任务。当前 STS 权限过宽
按 3.9.6 记录为后续安全任务，不阻断本轮运行实现。

本轮只确认处理策略并记录决策，未修改运行代码。

## 4. 事件决策

### 4.1 `deviceCommandObserved`

状态：**已确认**

#### 4.1.1 什么时候发生

`deviceCommandObserved` 在一个可识别设备命令跨过重要状态边界时产生。物模型定义六个
阶段：

- `RECEIVED`：香橙派刚收到命令；
- `ACCEPTED`：命令通过校验并已可靠保存到 SQLite；
- `REJECTED`：命令身份可信、可解释，但在受理前被明确拒绝；
- `MCU_ACCEPTED`：MCU 接受命令；fixed-frame 按本节兼容语义处理；
- `PRE_START_FAILED`：命令已经受理，但能证明 UART 尚未写出、物理动作不可能开始时
  失败；
- `FAILED`：命令可能已经写给 MCU，随后执行失败、结果超时或香橙派重启。

项目负责人确认当前不产生 `RECEIVED`。同步服务回执和后续 `ACCEPTED/REJECTED` 已经
覆盖当前运行所需信息，省略该阶段可以减少无业务增量的可靠事件。

并非每条命令都必须经历或上报全部阶段；成功终态由对应的更强完成事件表达。

#### 4.1.2 有什么作用

该事件把设备命令进度变成需要后端业务确认的 `RELIABLE_FACT`，用于严格区分：

1. OneNet 只完成了传输；
2. 香橙派已经可靠保存命令；
3. 命令已经越过可能产生物理动作的边界；
4. 命令在开始前或执行中失败；
5. 投递、清运、满溢或基准测量已经真正完成。

`ACCEPTED` 和 `MCU_ACCEPTED` 都不是业务完成。成功结果仍分别由
`deliveryComplete`、`cleanComplete`、`fullnessSampleComplete` 和
`baselineMeasurementComplete` 收敛。本事件不得承载投递按钮、中间轮次、开关门、
中间重量或照片过程。

#### 4.1.3 适用命令

当前物模型只允许本事件观察以下六种领域命令：

- `START_DELIVERY_SESSION`；
- `START_CLEAN_OPERATION`；
- `END_CLEAN_BEFORE_UNLOCK`；
- `RESUME_CLEAN_OPERATION`；
- `SAMPLE_FULLNESS`；
- `MEASURE_EMPTY_BAG_BASELINE`。

以下三个服务不使用本事件：

- `applyConfiguration` 使用 `configurationProgress`；
- `confirmEdgeEvent` 使用 `businessConfirmationReceipt`；
- `providePhotoUploadGrant` 使用同步回执及照片请求/状态事件。

#### 4.1.4 fixed-frame 阶段策略

##### `startDeliverySession` / `startCleanOperation`

1. 完整校验并将命令上下文、工作槽可靠写入 SQLite 后，创建唯一 `ACCEPTED`。
2. `BB+AA` 或 `EE` 完整写出后，创建兼容 `MCU_ACCEPTED`。
3. UART 写出前能确定失败时，创建 `PRE_START_FAILED`。
4. UART 可能已部分或完整写出、等待 `DD/EF` 超时或香橙派在活动作业中重启时，
   创建 `FAILED`。
5. 正常收到 `DD/EF` 时只创建相应完成事件，不再创建不存在于物模型中的“成功”
   命令观察。
6. `FAILED` 后继续遵守已确认的 fixed-frame 规则：本地作业置失败、释放工作槽，
   不重发物理启动命令；后端也不得自动重放原命令。

##### `endCleanBeforeUnlock` / `resumeCleanOperation`

fixed-frame 在同步受理阶段直接返回
`REJECTED / MCU_FEATURE_NOT_SUPPORTED`，并为该可解释拒绝创建唯一
`deviceCommandObserved: REJECTED`。命令不进入普通执行队列，不产生
`ACCEPTED/PRE_START_FAILED`。

##### `sampleFullness` / `measureEmptyBagBaseline`

- 正常本地完成时直接创建各自完成事件，不额外创建 `ACCEPTED`；
- 在同步受理阶段对可解释的非法命令创建 `REJECTED`；
- 已同步受理、但本地执行随后发生真实失败时创建 `FAILED`；
- 无历史重量已经分别确认使用 0 克兼容值，因此不属于失败。

#### 4.1.5 fixed-frame `MCU_ACCEPTED` 兼容语义

fixed-frame MCU 没有 ACK。项目负责人根据“当前必须先跑起来、允许构造 MCU 正常状态”
的共同前提确认：

> 香橙派完整写出 `BB+AA` 或 `EE` 后，允许构造正常的
> `deviceCommandObserved: MCU_ACCEPTED`。

该事件使用香橙派生成的兼容 `mcuCommandUid`。其当前含义是“香橙派已把完整固定帧交给
串口驱动”，不是 MCU 返回了真实 ACK。内部诊断必须保留
`acceptance_basis=FIXED_FRAME_WRITE_COMPLETE`，但 OneNet 事件按既有物模型上报正常
`MCU_ACCEPTED`。

若写入异常且不能证明零字节写出，不得产生 `MCU_ACCEPTED`，而应产生 `FAILED`；只有
能够证明没有任何物理命令字节写出时才使用 `PRE_START_FAILED`。任何情况都不自动重发
旧物理启动帧。

#### 4.1.6 拒绝和失败边界

- `DEVICE_BUSY`、`portNo` 不支持、配置身份不匹配、命令已过期和 fixed-frame
  能力不支持等，在命令身份和摘要可信时可以产生 `REJECTED`。
- 命令缺少合法 `commandUid`、摘要无法验证或连稳定身份都不可解释时，只回复服务拒绝，
  不创建本事件。
- 拍照失败、COS 授权缺失或上传失败不属于命令启动失败，不得产生
  `PRE_START_FAILED/FAILED`。
- `PRE_START_FAILED` 只用于能够证明物理命令从未可能执行的情况。
- `FAILED` 用于已经越过该证明边界、但未形成更强完成事件的终态失败。
- 所有失败错误码使用最长 64 字符的大写稳定符号，不把异常正文、密钥或原始报文写入
  事件。

#### 4.1.7 事件身份、幂等与确认

每条事件必须：

- `target.type=DEVICE_COMMAND`；
- `target.uid == commandUid`；
- `payload.observedCommandType` 与原命令一致；
- `RECEIVED/ACCEPTED` 的 `mcuCommandUid/errorCode` 均为空；
- `REJECTED` 的 `mcuCommandUid` 为空且 `errorCode` 必填；
- `MCU_ACCEPTED` 的 `mcuCommandUid` 必填且 `errorCode` 为空；
- `PRE_START_FAILED/FAILED` 的 `errorCode` 必填，`mcuCommandUid` 按是否已经分配保留。

同一 `commandUid + stage` 只能对应一个稳定 `eventUid`、一个
`edgeEventSequence` 和一份不可变载荷。技术重发复用原事件；同阶段异内容不能覆盖。

每条实际创建的 `deviceCommandObserved` 都是独立 `RELIABLE_FACT`，必须进入 SQLite
发件箱持续上报。后端完成命令状态归并后，通过已确认的 `confirmEdgeEvent` 逐事件确认。

#### 4.1.8 当前实现判断与保留任务

香橙派当前没有任何 `DEVICE_COMMAND_OBSERVED` 运行时创建路径。同步服务会先回复，
异步执行失败只更新本地 `command_inbox` 并写日志，因此后端看不到设备忙、不支持、
UART 写失败、结果超时或重启失败。

后续实施需要：

1. 增加事件构造器和 `(commandUid, stage)` 稳定身份约束；
2. 在命令受理、拒绝、UART 派发及失败事务中原子创建对应事件；
3. fixed-frame 完整写出时生成兼容 `mcuCommandUid` 和 `MCU_ACCEPTED`；
4. 确保照片失败不被误映射为命令失败；
5. 重启恢复为活动投递/清运创建唯一 `FAILED`，但不重放命令；
6. 补齐重复、乱序、同阶段冲突、部分 UART 写入和强杀进程测试。

后端当前也完全不消费该事件。后续需要注册处理器，校验可信来源、Schema、摘要、目标和
原命令身份，幂等保存事件并推进命令状态；完成事件处理后为每个
`deviceCommandObserved` 创建唯一业务确认。后端归并必须服从本文已经确认的
fixed-frame 释放和禁止重放规则。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.2 `configurationProgress`

状态：**已确认**

#### 4.2.1 什么时候发生

`configurationProgress` 只用于报告 `applyConfiguration` 的可靠处理进度，包含三个阶段：

- `EDGE_SAVED`：完整配置、版本和双摘要已经可靠写入香橙派 SQLite；
- `APPLIED`：该配置版本已经成为香橙派当前活动配置；
- `FAILED`：配置命令已经受理，但后续保存或活动版本切换发生真实失败。

正常路径固定按 `EDGE_SAVED → APPLIED` 产生两个事件。两个阶段在时间上可以紧邻，但
不能合并为一个事件，也不能用 OneNet 同步服务回执代替。

#### 4.2.2 有什么作用

该事件用于让后端区分四个不同事实：

1. OneNet 已经接收服务调用；
2. 香橙派已经可靠保存完整配置；
3. 该配置已经在香橙派本地生效；
4. 已受理的配置处理最终失败。

它是需要业务确认的 `RELIABLE_FACT`。后端只有收到并归并 `APPLIED`，才能把本次配置
应用记为成功；`EDGE_SAVED` 只是中间状态，不能替代最终应用结果。

#### 4.2.3 fixed-frame 阶段语义

项目负责人基于“当前首要目标是跑起来、冻结 MCU 无法应用新增配置”的事实确认：

> `APPLIED` 只证明配置已经成为香橙派活动配置，不证明 MCU 收到或应用了配置。

具体规则如下：

- 配置处理不向 MCU 发送 UART 配置帧，也不等待 MCU ACK；
- MCU 不支持配置投影不属于失败条件；
- `mcuPayloadSha256` 只保留为配置身份和完整性字段；
- `APPLIED.mcuCommandUid` 由 fixed-frame 兼容层生成，以满足现有物模型；
- 内部诊断保留 `mcu_configuration_projection=NOT_SUPPORTED`；
- 后端、Web 和运维界面不得把该 `APPLIED` 描述为“MCU 已同步”或“物理执行成功”。

#### 4.2.4 事件字段约束

事件必须满足：

- `target.type=CONFIGURATION_APPLICATION`；
- `target.uid == payload.applicationUid`；
- 封套中的 `commandUid` 指向原 `applyConfiguration` 命令；
- 三个阶段都携带同一配置的 `applicationUid`、`version`、`contentSha256` 和
  `mcuPayloadSha256`；
- `EDGE_SAVED` 的 `mcuCommandUid/errorCode` 均为空；
- `APPLIED` 的兼容 `mcuCommandUid` 必填，`errorCode` 为空；
- `FAILED` 的稳定 `errorCode` 必填，`mcuCommandUid` 按失败时是否已经分配保留。

错误码使用最长 64 字符的大写稳定符号，不把异常正文、密钥或原始配置写入事件。

#### 4.2.5 拒绝和失败边界

- 命令封套、目标、字段、配置摘要、版本或配置身份在受理前不合法时，同步返回
  `REJECTED`，不产生 `configurationProgress`。
- 命令已经受理，随后发生真实的 SQLite 保存或活动版本切换失败时，产生
  `FAILED`。
- 如果 SQLite 整体不可写，导致配置状态和事件都无法可靠持久化，则服务必须返回失败，
  不得构造虚假的 `EDGE_SAVED/APPLIED`。
- “MCU 不支持配置投影”不产生 `FAILED`。

#### 4.2.6 幂等、重启与业务确认

- 同一配置应用的同一阶段只能创建一次，技术重发必须复用已经持久化的
  `eventUid`、`edgeEventSequence` 和不可变载荷。
- 相同命令或相同配置的幂等重试不得重新创建一套进度事件。
- 重启时若已经存在 `EDGE_SAVED`、但尚无 `APPLIED`，香橙派继续完成本地活动版本切换，
  并创建或补发缺失的原 `APPLIED`，不得向 MCU 写帧。
- 已经创建的事件若尚未得到业务确认，重启和断网后仍须持续重发同一个事件。
- `EDGE_SAVED`、`APPLIED` 和 `FAILED` 中每一条实际创建的事件都独立等待
  `confirmEdgeEvent`，不能只确认最终阶段。

#### 4.2.7 后端归并语义

后端收到事件后必须校验可信来源、Schema、目标、原配置申请、命令身份、版本和双摘要，
然后幂等保存事件并更新配置应用投影：

- `EDGE_SAVED`：记录香橙派已持久化，但不结束配置可靠任务；
- `APPLIED`：记录香橙派活动配置已切换，并结束本次配置可靠任务；
- `FAILED`：记录配置应用失败并结束本次配置可靠任务。

`APPLIED` 不得写入或展示为 MCU 同步时间、MCU 物理成功状态或 MCU 实际应用证明。
每个阶段完成业务归并后，后端都创建该 `eventUid` 对应的唯一
`confirmEdgeEvent`。

#### 4.2.8 当前实现判断与保留任务

香橙派当前 fixed-frame 正常路径已经实现：

- 配置、版本、双摘要和 `EDGE_SAVED` 在 SQLite 事务中保存；
- 随后生成兼容 `mcuCommandUid`，在本地切换活动配置并产生 `APPLIED`；
- 全程不写 UART，并记录 `mcu_configuration_projection=NOT_SUPPORTED`；
- 重启可继续处理停在 `EDGE_SAVED` 的配置。

当前仍有以下实现缺口：

1. 同步服务在只写入通用命令收件箱后就返回 `ACCEPTED`，完整配置校验发生得更晚；
   后续校验失败时可能既没有同步拒绝，也没有可靠的 `FAILED`。
2. 相同配置换用新 `commandUid` 重试时，新命令可能停在 `PROCESSING`；同版本、同摘要、
   不同 `applicationUid` 的幂等规则也未完全符合已确认口径。
3. 已发布为 `SENDING`、尚未获业务确认的事件在重启后不会自动恢复发送。
4. 后端虽然已经完整消费本事件，但会把兼容 `APPLIED` 写成 `mcu_synced_at` 和
   `PHYSICAL_SUCCEEDED`，与本节语义冲突。
5. 后端处理 `FAILED` 时尚未同步把原设备命令归并为失败。
6. 后端尚未为每个进度事件创建 `confirmEdgeEvent`，也未完成确认回执闭环。

后续实施需要保留现有可信事件校验和配置投影归并链，修正上述状态含义、失败归并、
幂等重试、发件箱恢复和逐事件业务确认，并覆盖正常两阶段、重复、乱序、失败和进程强杀
测试。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.3 `deliveryComplete`

状态：**已确认**

#### 4.3.1 什么时候发生

`deliveryComplete` 只在当前唯一活动投递会话收到一条合法
`DD PRE POST FULL DD`，并把该结果可靠绑定到原 `sessionUid` 后产生。

在事件首次上报前，香橙派必须先可靠保存 `DD` 原始结果、整场完成事实、稳定事件身份和
发件箱记录。一个 `sessionUid` 最多产生一条 `deliveryComplete`。

以下情况不产生本事件：

- `DD` 帧格式错误、重量超出固定帧合法范围或缺少可解释的接收身份；
- 当前没有活动投递时收到迟到或重复 `DD`；
- 等待 `DD` 超时；
- fixed-frame 投递过程中香橙派重启；
- 没有取得可信 `DD` 结果，只能判断原物理命令可能已经执行。

等待结果超时或 fixed-frame 重启使用已经确认的
`deviceCommandObserved: FAILED`，本地会话置失败并释放工作槽，不构造
`DEVICE_INTERRUPTED` 投递完成事件，也不创建零重量替代单。

#### 4.3.2 有什么作用

该事件是一个投递会话的唯一整场最终事实，供后端：

1. 保存首次开门前和最终关门后的总重量；
2. 按 `sessionUid` 创建唯一投递订单；
3. 固化开始时的配置、业务单价和四个整场照片槽；
4. 保存设备侧兼容结束原因、门命令事实和负重量标志；
5. 建立投递后满溢检测 gate 和必要的 `sampleFullness` 任务；
6. 完成会话业务归并并为本事件建立 `confirmEdgeEvent`。

它不承载投递中间按钮、轮次、开关门过程、中间重量或中间照片，也不直接承载满溢度。

#### 4.3.3 fixed-frame 字段映射

项目负责人确认当前按以下兼容语义构造：

```text
firstPreOpenMeasurement.reportedWeightGrams = DD.PRE
finalPostCloseMeasurement.reportedWeightGrams = DD.POST
deliveryNetWeightGrams                     = DD.POST - DD.PRE
completionReason                           = USER_ENDED
manualReviewRequired                       = false
negativeWeightAnomaly                      = false
```

其中：

- `PRE`、`POST` 是 MCU 实际返回的无符号总重量，香橙派不改写这两个数值；
- 净重保留有符号结果，不截断为 0，后端按首末重量重新计算并严格核对；
- 固定帧没有测量质量元数据，兼容层将两个测量构造为
  `STABLE / OK / STABLE_WINDOW_MEAN`，其他必填质量字段使用合法的正常占位值；
- 两个 `measurementUid` 和事件 `eventUid` 必须是持久化的合法 UUIDv4；
- `completionReason=USER_ENDED` 是当前无法区分真实结束按钮和本地选择窗口届满时使用的
  正常兼容值；
- `manualReviewRequired=false` 只表示本事件不是设备中断结果，不取消 P0 投递订单原有的
  人工审核；
- `negativeWeightAnomaly=false` 是已确认的 fixed-frame 兼容值，即使整场净重为负也不在
  设备侧改为 `true`；
- `frozenConfig` 和 `unitPriceTenThousandths` 必须来自原
  `startDeliverySession` 保存的快照，不能读取完成时的当前配置或价格。

最终门命令固定构造为：

```text
command            = CLOSE
outputStatus       = COMMAND_DISPATCHED
physicalStateBasis = NOT_OBSERVABLE
```

该对象只表达 fixed-frame 按正常关门流程完成的兼容命令事实，不是 MCU 提供的真实门位
`CLOSED` 证明。后端和运维界面不得把它改写成真实门磁或门位健康事实。

#### 4.3.4 照片与 `DD.FULL`

完成事件固定携带以下四个照片槽，顺序为：

```text
BEFORE_INNER
BEFORE_OUTER
AFTER_INNER
AFTER_OUTER
```

项目负责人进一步确认：

> `deliveryComplete` 上报时直接携带四个已经冻结的正式目标 URL。URL 是照片将要写入的
> 确定性 COS 对象地址，不是“对象已经上传成功”的证明。

四个槽的 `photoUid`、对象 key 和正式 URL 必须在相应拍摄动作开始前可靠预分配。正式
URL 使用可信 COS 基础地址以及以下身份共同推导：

```text
deploymentCode + DELIVERY_SESSION + sessionUid + slot + photoUid
```

事件不等待拍照或 COS 上传完成。每个槽按事件创建时已经可靠保存的最新事实填充，但无论
当时是 `AVAILABLE`、`UPLOAD_PENDING` 还是已经确定缺失，完成事件中的四个槽都保留各自
预分配的正式目标 URL：

- 已上传时使用 `AVAILABLE`，并携真实摘要、照片身份和该正式 URL；
- 已拍摄但尚未上传时使用 `UPLOAD_PENDING`，保留本地照片身份、摘要和同一个正式 URL；
- 尚未拍到或状态仍待收敛时使用待处理状态，同时携预分配照片身份和正式 URL；
- 已确定无法取得时使用 `PERMANENTLY_MISSING` 和稳定缺失原因，但完成事件中保留的 URL
  仍只表示原预留对象地址，不能当作对象存在；
- 后续终态变化由 `photoStatusReported` 报告，不修改原完成事件。

照片后来上传失败或重新取得授权时，必须继续使用完成事件中的同一
`photoUid + objectKey + URL` 重试上传，不得换 URL、换对象 key 或生成第二个照片身份。
后续 `photoStatusReported: AVAILABLE` 携带的 URL 必须与完成事件预留 URL 完全一致。

投递前后拍照失败、授权缺失和上传失败都不阻止 `deliveryComplete`、建单或业务确认，也
不因为缺图自动禁止返现。订单仍执行正常人工审核，审核通过后才按既有资金规则返现。

当前契约把 `UPLOAD_PENDING.url` 和 `PERMANENTLY_MISSING.url` 限制为 `null`，与上述
新确认口径不一致。后续实施必须调整完成事件专用照片快照 Schema、OneNet 物模型、线格式
映射、示例和语义校验；不得为了暂时通过旧 Schema 而把尚未上传的照片伪报成
`AVAILABLE`。

`DD.FULL` 只作为最新红外原始观测保存在香橙派，不进入
`deliveryComplete`。后端在建单事务中建立投递后满溢检测，随后通过
`sampleFullness` 取得已经确认的重量满溢度结果。

#### 4.3.5 后端归并语义

后端必须先校验可信 OneNet 来源、事件 Schema、摘要、部署、目标、原命令和原会话，并
要求：

- `target.type=DELIVERY_SESSION`；
- `target.uid == payload.sessionUid`；
- `commandUid` 对应原 `START_DELIVERY_SESSION`；
- `portNo`、冻结配置和原始单价与原会话完全一致；
- 两个测量为当前 fixed-frame 允许的正常兼容形状；
- `deliveryNetWeightGrams == POST - PRE`；
- 四个照片槽名称、数量和顺序正确。

业务事务以 `sessionUid` 作为完成结果和订单的唯一根。`eventUid` 只用于事件幂等和冲突
检测，不能作为创建第二笔订单的理由。同一事务至少完成：

1. 保存唯一物理结果及设备自报净重；
2. 创建且仅创建一笔投递订单和四个照片槽；
3. 保存 `negativeWeightAnomaly=false` 和兼容门命令事实；
4. 使用开始时冻结的袋、用户、配置和业务单价；
5. 建立投递后满溢检测 gate 和必要采样任务；
6. 结束会话并释放后端匹配占位；
7. 将可信 inbox 标记为已处理；
8. 创建本 `eventUid` 的唯一 `confirmEdgeEvent` 意图。

用户归属优先使用原投递会话开始时保存的用户关系。当前兼容链路若没有有效用户归属，
仍创建无主单且不返现；迟到事件不得借用后来用户。完成事件只创建待审核订单，不直接
写钱包，缺图也不阻止后续正常审核和审核通过后的返现。

#### 4.3.6 幂等、释放与业务确认

- 完成事实、稳定 `eventUid`、`edgeEventSequence` 和发件箱行必须先可靠提交，之后才能
  释放香橙派工作槽。
- 工作槽释放不等待 MQTT、OneNet 或后端业务确认，避免断网永久占用设备。
- 同一 `sessionUid` 的重复 `DD` 或重复处理只能复用原完成事实，不分配第二事件。
- MQTT、OneNet 回执丢失、断网或重启后持续重发同一个 `eventUid` 和不可变载荷。
- 后端同事件同摘要重投复用原订单和原确认；同事件异摘要、同部署序号异事实或同
  `sessionUid` 的第二完成事件必须隔离，不能覆盖首个可信结果。
- 香橙派收到匹配的 `confirmEdgeEvent` 后才把原事件置为业务已确认。

#### 4.3.7 接受的迟到 `DD` 残余风险

固定帧 `DD` 不包含 `sessionUid`、命令身份或作业序号。旧会话已经超时或因重启释放后，
如果它的迟到 `DD` 恰好在新会话占用工作槽之后到达，香橙派没有字段可以证明它属于旧
会话，存在错绑到新会话的可能。

项目负责人基于“当前首要目标是跑起来”确认接受该残余风险。当前只要求：

- 整机同时只有一个活动工作槽；
- 每次新投递发送 `BB+AA` 前清空当时已经到达的串口缓存；
- 没有活动投递时忽略 `DD`；
- 不增加长时间冷却锁，不修改冻结 MCU，也不自动重放物理命令。

该措施不能消除在新 `AA` 写出之后才到达的旧 `DD`；本文明确记录它是 fixed-frame 能力
边界，不将其描述为已解决。

#### 4.3.8 当前实现判断与保留任务

香橙派当前 fixed-frame 正常路径已经能够：

- 单次发送 `BB+AA`；
- 把合法 `DD` 绑定当前唯一投递槽；
- 保存 `PRE/POST`，计算 `POST-PRE` 并缓存 `FULL`；
- 构造 `USER_ENDED`、`negativeWeightAnomaly=false` 和不可观测门命令事实；
- 生成一条 `DELIVERY_COMPLETE` 后释放工作槽。

当前仍有以下端到端阻塞或可靠性缺口：

1. fixed-frame 使用 UUIDv5 构造事件及测量身份，而正式 Schema 强制 UUIDv4；当前测试
   只验证 OneNet 投影编码，没有对运行时事件执行正式 Schema 校验。
2. 当前兼容测量使用 `sampleCount=0`；Schema 允许该值，但后端物理结果表要求稳定测量
   至少一个样本，无法原样落库。
3. `DD` 收件、命令终态、工作上下文、`FULL`、照片、完成事件、MCU 收件终态和工作槽
   释放分散在多个事务。断电可能发生在 `DD` 已入库而完成事件尚未建立的窗口，重启后
   该结果可能被当作无活动作业结果忽略。
4. 在线等待 `DD` 超时尚无定时处理，会永久占用工作槽。
5. 完成事件目前始终把四个照片槽写成空元数据的 `UPLOAD_PENDING`，没有读取已经落盘
   的真实照片状态；照片终态事件还可能先于完成事件产生，形成状态回退或暂存依赖。
6. 普通可靠事件发布后进入 `SENDING`，尚无重启恢复和持续重发路径。
7. fixed-frame 合成的 MCU 接收序号会在进程重启后重复，可能把新的相同内容首帧误判为
   历史重复。
8. 后端当前只消费 `configurationProgress`。`deliveryComplete` 会在分发器警告后被 MQ
   ACK，既不进入可信 inbox，也没有物理结果、订单、照片或满溢 gate 的业务写入用例。
9. 后端数据库与当前事件仍存在直接冲突：投递结果表要求真实
   `CLOSED/OK`，稳定测量样本数要求大于零，部分照片状态无法无损保存，投递会话和订单
   用户字段又不允许无主值。
10. 后端尚无本事件的 `confirmEdgeEvent` 下发及确认回执闭环。
11. 原 `START_DELIVERY_SESSION` 仍缺少
    `target.uid == payload.sessionUid` 的专项语义校验。
12. 保留的 UART-v1 重启分支仍可构造 `DEVICE_INTERRUPTED` 完成事件；该行为不能应用到
    本节确认的 fixed-frame 路径。

后续实施必须优先打通 Schema 合法身份、`DD → 唯一完成事件` 原子恢复、在线超时、
真实照片槽快照、可靠重发、后端可信消费、唯一建单、数据库兼容和业务确认，并覆盖强杀
进程、迟到/重复 `DD`、同会话第二事件、照片乱序和无主单测试。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.4 `cleanComplete`

状态：**已确认**

#### 4.4.1 什么时候发生

`cleanComplete` 只在当前唯一活动清运操作收到一条合法
`EF PRE POST FULL EF`，并把该结果可靠绑定到原 `operationUid` 后产生。

`startCleanOperation` 已返回受理、香橙派已写出 `EE 01 EE`、清运前照片已处理或清运员
已经开始换袋，都不代表清运已经完成。只有合法 `EF` 才是 fixed-frame 路径的完成事实。

在事件首次上报前，香橙派必须先可靠保存：

1. `EF` 原始结果及其接收身份；
2. 原清运操作和 `newBagUid` 的关联；
3. `EF.POST` 对应的新袋皮重；
4. 稳定的事件及测量 UUIDv4；
5. 不可变事件载荷和可靠发件箱记录。

保存成功后才能释放本地清运工作槽。一个 `operationUid` 最多产生一条
`cleanComplete`。

以下情况不产生本事件：

- `EF` 帧格式错误、重量超出 fixed-frame 合法范围或缺少可解释的接收身份；
- 当前没有活动清运操作时收到迟到或重复 `EF`；
- 等待 `EF` 超时；
- fixed-frame 清运过程中香橙派重启；
- 没有取得可信 `EF`，只能判断原 `EE` 可能已经写给 MCU。

等待结果超时或 fixed-frame 重启沿用已经确认的降级策略：原设备命令置
`deviceCommandObserved: FAILED`，本地清运操作失败并释放工作槽，不构造
`cleanComplete`，后端也不得据此执行袋交换或建立新皮重。

#### 4.4.2 有什么作用

该事件是 fixed-frame 清运和换袋完成的唯一可靠业务事实，供后端：

1. 保存首次解锁前总重量和清运员确认换袋后的最终总重量；
2. 按原清运操作冻结的旧皮重核算本次移除净重；
3. 创建唯一清运记录；
4. 将旧袋从端口解绑，并把原操作预留的新袋绑定到端口；
5. 使用最终重量建立新袋皮重；
6. 保存四个清运照片槽及人工关门兼容事实；
7. 完成清运操作、释放后端占用并建立清运后满溢检测；
8. 为本事件建立 `confirmEdgeEvent`。

它不表示 `EE` 已获得 MCU ACK，不提供真实门磁、真实电磁锁健康度或独立称重质量，也
不直接承载满溢百分比。

#### 4.4.3 fixed-frame 重量与完成状态映射

项目负责人确认按以下公式构造：

```text
preUnlockMeasurement.reportedWeightGrams              = EF.PRE
cleanerConfirmedFinalMeasurement.reportedWeightGrams  = EF.POST
removedNetWeightGrams                                 = EF.PRE - oldBaselineWeightGrams
newBaselineWeightGrams                                = EF.POST
```

其中：

- `EF.PRE` 表示首次解锁前的实际总重量；
- `EF.POST` 表示清运员完成换袋并确认关门后的最终总重量；
- `oldBaselineWeightGrams` 必须来自原 `startCleanOperation` 已可靠保存的冻结快照；
- 有旧皮重数值时，移除净重保留有符号结果，不截断为 0；
- 旧皮重数值缺失时，`removedNetWeightGrams=null`，不得退化为
  `EF.PRE - EF.POST`；
- 旧皮重缺失不阻止清运完成、袋交换或新皮重建立，`EF.POST` 仍保存为
  `newBagUid` 的当前皮重；
- 后端必须使用原操作冻结的旧皮重重新计算并核对设备值，同时保留旧皮重的可信、
  不可信或缺失状态；
- 两个测量和事件身份必须是持久化的合法 UUIDv4；
- fixed-frame 没有测量质量元数据，兼容层将合法 `EF` 的两个重量构造为当前允许的
  正常稳定测量形状。

禁止继续使用下列旧公式：

```text
removedNetWeightGrams = EF.PRE - EF.POST
```

清运完成和门锁状态固定构造为：

```text
cleanerCompletionConfirmed                  = true
cleanActionSequence                         = 1
lockPowerState                              = DEENERGIZED
solenoidHealth                              = UNKNOWN
physicalDoorStateBasis                      = CLEANER_CONFIRMATION
cleanerPhysicalCloseConfirmed               = true
```

这些值表达 fixed-frame 单次 `EE` 流程的正式兼容事实。`UNKNOWN` 是当前无法取得电磁锁
健康证明的正常兼容值；`CLEANER_CONFIRMATION` 表示人工确认关门。后端真实物理门位必须
继续保存为 `UNKNOWN`，不得从断电状态推导或伪造成门磁检测到 `CLOSED`。

事件中的 `portNo`、`oldBagUid`、`newBagUid` 和 `frozenConfig` 必须来自原
`startCleanOperation` 快照，不能读取完成时可能已经变化的当前配置或袋状态。

#### 4.4.4 照片与 `EF.FULL`

完成事件固定携带以下四个照片槽，顺序为：

```text
FIRST_OPEN_INNER
FIRST_OPEN_OUTER
FINAL_CLOSE_INNER
FINAL_CLOSE_OUTER
```

事件不等待拍照或 COS 上传完成。每个槽按事件创建时已经可靠保存的最新事实填充：

- 已上传时使用 `AVAILABLE` 并携真实 URL、摘要和照片身份；
- 已拍摄但尚未上传时使用 `UPLOAD_PENDING` 并保留本地照片身份和摘要；
- 尚未拍到或状态仍待收敛时使用契约允许的待处理形状和稳定原因；
- 已确定无法取得时使用 `PERMANENTLY_MISSING` 和稳定缺失原因；
- 后续终态变化由 `photoStatusReported` 报告，不修改或回退原完成事件。

清运前、清运后任意照片失败、授权缺失或上传失败，都不阻止写出 `EE`、接收 `EF`、
创建 `cleanComplete`、生成清运记录、执行袋交换、建立新皮重或触发后续满溢检测。

`EF.FULL` 只作为 MCU 原始红外观测保存在香橙派，不进入 `cleanComplete`，也不直接
解释为满溢百分比。后续满溢度仍由已经确认的 `sampleFullness` 处理策略计算。

#### 4.4.5 后端归并语义

后端必须先校验可信 OneNet 来源、事件 Schema、摘要、设备部署、目标、原命令和原清运
操作，并要求：

- `target.type=CLEAN_OPERATION`；
- `target.uid == payload.operationUid`；
- `commandUid` 对应同一操作的原 `START_CLEAN_OPERATION`；
- `portNo`、旧袋、新袋和冻结配置与原操作快照一致；
- 新袋仍是该操作预留的 `CLEAN_RESERVED` 袋；
- 两个测量符合当前 fixed-frame 允许的正常兼容形状；
- 有冻结旧皮重时，
  `removedNetWeightGrams == PRE - oldBaselineWeightGrams`；
- 冻结旧皮重缺失时，`removedNetWeightGrams == null`；
- `newBaselineWeightGrams == POST`；
- 完成、门锁、人工确认字段和四个照片槽符合本节固定映射。

业务事务以 `operationUid` 作为清运结果和袋交换的唯一根。`eventUid` 只用于事件幂等和
冲突检测，不能作为重复换袋的理由。同一事务至少完成：

1. 保存唯一清运物理结果及设备自报净重；
2. 创建且仅创建一条清运记录和四个照片槽；
3. 保存设备值，并按原操作冻结旧皮重重新核算移除净重；
4. 将原旧袋解除端口占用并记录清运移除事实；
5. 将该操作预留的新袋转为端口当前袋并记录清运安装事实；
6. 使用 `POST` 建立新袋皮重；
7. 建立清运完成后的满溢检测 gate 和必要采样任务；
8. 完成清运操作并释放后端匹配占位；
9. 将可信 inbox 标记为已处理；
10. 创建本 `eventUid` 的唯一 `confirmEdgeEvent` 意图。

旧皮重缺失时只把移除净重记为不可计算，不回滚换袋、新袋绑定或 `POST` 新皮重。照片
缺失同样不阻断上述事务。

#### 4.4.6 幂等、释放与业务确认

- `EF` 原始结果、新袋皮重、稳定事件身份和发件箱记录必须先可靠提交，之后才能释放
  香橙派清运工作槽。
- 工作槽释放不等待 MQTT、OneNet 或后端业务确认，避免断网永久占用设备。
- 同一 `operationUid` 的重复 `EF` 或处理重入只能复用原完成事实，不创建第二事件、
  第二清运记录或再次交换袋。
- MQTT、OneNet 回执丢失、断网或重启后持续重发同一个 `eventUid` 和不可变载荷。
- 后端同事件同摘要重投复用原清运结果和原确认；同事件异摘要、同部署序号异事实或同
  `operationUid` 的第二完成事件必须隔离，不能覆盖首个可信结果。
- 香橙派收到匹配的 `confirmEdgeEvent` 后才把原事件置为业务已确认。

#### 4.4.7 接受的迟到 `EF` 残余风险

固定帧 `EF` 不包含 `operationUid`、命令身份或作业序号。旧清运操作已经超时或因重启
释放后，如果它的迟到 `EF` 恰好在新清运操作占用工作槽之后到达，香橙派没有字段可以
证明它属于旧操作，存在错绑到新操作的可能。

项目负责人基于“当前首要目标是跑起来”确认接受该残余风险。当前只要求：

- 整机同时只有一个活动工作槽；
- 每次新清运写出 `EE` 前清空当时已经到达的串口缓存；
- 没有活动清运操作时忽略 `EF`；
- 不增加长时间冷却锁，不修改冻结 MCU，也不自动重放 `EE`。

该措施不能消除在新 `EE` 写出之后才到达的旧 `EF`；本文明确记录它是 fixed-frame
能力边界，不将其描述为已解决。

#### 4.4.8 当前实现判断与保留任务

香橙派当前 fixed-frame 正常路径已经能够：

- 单次写出 `EE 01 EE`；
- 将合法 `EF` 绑定当前唯一清运槽；
- 保存 `PRE/POST` 并缓存 `FULL`；
- 构造已确认的人工完成、断电、人工关门依据和动作序号兼容值；
- 生成一条 `CLEAN_COMPLETE` 后释放工作槽。

当前仍有以下端到端阻塞或可靠性缺口：

1. fixed-frame 和保留的 UART-v1 完成构造器仍使用
   `PRE - POST`；契约语义校验器、示例和硬件测试也固化了旧公式，正确实现会被现有
   契约测试拒绝。
2. fixed-frame 使用 UUIDv5 构造事件及测量身份，而正式 Schema 和后端数据库要求
   UUIDv4。
3. fixed-frame 合成的稳定测量使用 `sampleCount=0`，后端数据库要求稳定测量至少一个
   样本；契约对部分失败测量状态也缺少完整成组约束。
4. 当前只把 `POST` 写入临时工作上下文、完成载荷和无袋身份的最新观测缓存，尚未形成
   可按 `newBagUid` 查询和恢复的本地皮重状态。
5. `EF` 收件、命令终态、新皮重、`FULL`、照片、完成事件、MCU 收件终态和工作槽释放
   分散在多个事务。断电可能发生在 `EF` 已入库而完成事件尚未建立的窗口，重启后该
   结果会因没有活动清运槽而被忽略。
6. 在线等待 `EF` 超时尚无定时处理，会永久占用工作槽。
7. 清运前照片持久化失败目前仍会阻止 fixed-frame 写出 `EE`；保留的 UART-v1 路径也会
   因前置照片失败停止解锁，违反照片非阻断决策。
8. 完成事件目前始终生成四个空元数据的 `UPLOAD_PENDING`，没有读取 PhotoManager
   已可靠保存的真实照片状态；照片终态事件可能先于完成事件产生。
9. 当前照片待上传形状携带 `PHOTO_METADATA_PENDING`，但数据库要求该状态
   `missing_reason` 为空；其他已拍摄待上传和永久缺失形状也不能被当前表约束无损保存。
10. fixed-frame 合成的 MCU 接收序号会在进程重启后重复，可能把新的相同内容首帧误判
    为历史重复。
11. 普通可靠事件发布后进入 `SENDING`，尚无重启恢复和业务确认前持续重发路径。
12. 后端当前只消费 `configurationProgress`。`cleanComplete` 会在分发器警告后被 MQ
    ACK，不进入可信 inbox，也不会创建清运记录、执行袋交换、建立新皮重或满溢 gate。
13. 后端清运结果表仍强制要求 `solenoidHealth=OK`、真实门位 `CLOSED`、门位依据
    `INFERRED_FROM_LOCK_POWER`，与本节确认的
    `UNKNOWN / CLEANER_CONFIRMATION / 人工关门确认` 直接冲突。
14. 后端表尚未完整保存 `cleanActionSequence` 和人工关门确认，清运照片字段长度及状态
    约束也与事件契约不完全一致。
15. 后端尚无本事件的可信消费用例、原操作上下文复算、唯一归并事务、
    `confirmEdgeEvent` 下发及确认回执闭环。
16. 后续 `sampleFullness` 复用 `FULL` 缓存时尚未绑定来源作业、接收时间和配置代际；
    该问题留到 `fullnessSampleComplete` 事件继续收口。

后续实施必须优先统一净重公式和契约生成物，打通 UUIDv4 合法身份、
`EF → 新袋皮重 → 唯一完成事件` 的原子恢复、照片非阻断和真实快照、在线超时、可靠
重发、后端可信消费、唯一袋交换、数据库兼容和业务确认，并覆盖强杀进程、迟到/重复
`EF`、同操作第二事件、旧皮重缺失和照片乱序测试。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.5 `fullnessSampleComplete`

状态：**已确认**

#### 4.5.1 什么时候发生

`fullnessSampleComplete` 不在香橙派收到 `DD` 或 `EF` 时自动产生。完整时序是：

1. 后端因 `DELIVERY_COMPLETE`、`CLEAN_COMPLETE` 或人工重检建立一条
   `FULLNESS_DETECTION`；
2. 后端以稳定 `detectionUid` 和 `sampleRole` 调用 `sampleFullness`；
3. 香橙派可靠保存并受理命令；
4. fixed-frame 兼容层读取调用时 SQLite 中最近的合法 `DD.POST/EF.POST + FULL`；
5. 没有历史观测时使用已经确认的 0 克兼容值；
6. 香橙派冻结本角色使用的来源快照并立即创建本事件。

该路径完全在香橙派本地完成，不向 MCU 写任何 UART 帧，也不占用投递、清运或其他物理
工作槽。`INITIAL`、`CONFIRMATION` 和 `MANUAL_RECHECK` 三个角色均采用相同机制。

因此本事件表达的是“调用时最近流程结束观测的本地兼容检测结果”，不是一次新的实时
采样。事件时间表示本地结果形成时间，不能把缓存来源重量描述为该时刻重新测得的实时
重量。

在事件首次上报前，香橙派必须可靠保存：

- 原 `SAMPLE_FULLNESS` 命令及冻结配置；
- 本次角色选中的重量、红外位、来源作业和接收身份；
- 有无历史观测的兼容分支；
- 稳定 UUIDv4 事件及测量身份；
- 不可变载荷和可靠发件箱记录。

命令非法、摘要或目标不匹配、活动配置不匹配或本地持久化失败时不产生完成事件；没有
历史 `DD/EF` 本身不是失败条件。

#### 4.5.2 有什么作用

该事件供后端：

1. 保存一次 `(detectionUid, sampleRole)` 的唯一满溢样本；
2. 保存调用时最近的流程结束总重量和红外原始位；
3. 使用原检测冻结的满溢总重量计算显示百分比；
4. 按 `INITIAL/CONFIRMATION/MANUAL_RECHECK` 推进检测状态；
5. 更新投口容量投影及 `FULL/NOT_FULL` 结论；
6. 创建、延续或恢复持续满溢事件和对应运营投影；
7. 完成原采样命令，并为本事件建立 `confirmEdgeEvent`。

事件本身没有 `fullnessPercent` 字段，也不携带
`configuredFullWeightGrams`。后端必须从原检测和命令快照取得分母，不能读取处理事件
时可能已经变化的当前配置。

#### 4.5.3 调用时快照与事件字段

香橙派必须在执行本地命令时冻结“调用时最近观测”。如果结果创建过程中重启，恢复时
复用已经冻结的观测，不得改读重启后的新缓存并改变原事件。

有合法历史 `DD/EF` 时固定映射为：

```text
totalWeightMeasurement.reportedWeightGrams = latest DD.POST or EF.POST
fullnessSensorKind                         = DIGITAL_INFRARED
fullnessSensorValue                        = FULL == 1 ? BLOCKED : CLEAR
fullnessSampleBasis                        = NOT_SAMPLED
representativeDistanceMm                   = null
requestedSampleCount                       = 1
validSampleCount                           = 1
```

其中：

- 使用最后一条合法结果，不要求来源作业类型与 `triggerType` 同名；
- `DD.POST/EF.POST` 原值不扣皮重、不改写；
- 保留来源 `mcuBootId` 和 `mcuEventSequence`；
- `totalWeightMeasurement` 使用
  `STABLE / OK / STABLE_WINDOW_MEAN` 正常兼容形状；
- 嵌套重量测量的 `sampleCount=1`，表示本兼容结果携带一个可用重量值，不表示重新
  驱动 MCU 采样；
- `measurementUid` 和 `eventUid` 都是为本角色可靠保存的稳定 UUIDv4。

设备从未收到合法 `DD/EF` 时固定映射为：

```text
totalWeightMeasurement.reportedWeightGrams = 0
fullnessSensorKind                         = DIGITAL_INFRARED
fullnessSensorValue                        = CLEAR
fullnessSampleBasis                        = NOT_SAMPLED
representativeDistanceMm                   = null
requestedSampleCount                       = 0
validSampleCount                           = 0
```

0 克测量的其余质量字段和 MCU 身份使用持久化的正常兼容值，嵌套重量测量
`sampleCount=1`。后端通过
`NOT_SAMPLED + requestedSampleCount=0 + validSampleCount=0` 识别这是无历史默认值；
不修改当前 OneNet 9 服务 / 13 事件模型增加来源字段。

`sampleRole`、`triggerType`、`fullnessMode` 和 `frozenConfig` 必须逐字来自原命令及检测
快照。香橙派仍应在 SQLite 中保留来源作业类型、作业 UID、接收时间和配置代际，供
审计和重启恢复；这些本地元数据不改变本事件的 9/13 线上结构。

#### 4.5.4 百分比与 `FULL/NOT_FULL`

后端统一按已经确认的新 fixed-frame 公式计算：

```text
fullnessPercent =
    totalWeightMeasurement.reportedWeightGrams
    / detection.configuredFullWeightGrams
    × 100%
```

规则为：

- 不扣除 `currentBaselineWeightGrams`；
- 不把 `currentBaselineWeightGrams` 或 `fullnessMode` 用作公式输入；
- 使用十进制计算并对外保留两位小数；
- 允许超过 100%，不截顶；
- 无历史默认 0 克时结果为 0%；
- `fullnessPercent >= 100%` 判为 `FULL`；
- `fullnessPercent < 100%` 判为 `NOT_FULL`。

`DD/EF.FULL` 映射出的 `CLEAR/BLOCKED` 只作为红外原始辅助观测保存，不参与最终
`FULL/NOT_FULL`。`fullnessMode` 只作为原命令和检测快照留存，不改变 fixed-frame 最终
判定。

这意味着：

- 新空袋的 `EF.POST` 仍计入总重量百分比。例如 `POST=1200g`、满溢总重量
  `50000g` 时结果为 `2.40%`，不是扣皮重后的 `0%`；
- `BLOCKED` 但重量百分比不足 100% 时，最终仍为 `NOT_FULL`；
- 无历史的 0 克兼容结果最终为 `NOT_FULL`；
- 无历史 `CONFIRMATION` 或 `MANUAL_RECHECK` 也可以得到 `NOT_FULL` 并解除原满溢
  状态。

本节决策正式覆盖现有需求、UART 设计、接口设计和数据库中
`(totalWeight - baseline) / configuredFullWeight`、缺基准返回 `null` 以及按
`fullnessMode` 组合红外和重量的旧 fixed-frame 业务语义。实施时必须同步修改这些
上游文档、数据库约束和 API 计算，禁止通过伪填 `baseline=0` 绕过旧表约束。

#### 4.5.5 三种角色与检测状态

同一个 `detectionUid` 可以有多个角色结果：

- `INITIAL` 是投递后或清运后的首次兼容检测；
- `INITIAL` 得出 `NOT_FULL` 时可以终结检测；
- `INITIAL` 得出 `FULL` 时进入等待确认，并创建唯一
  `CONFIRMATION` 采样命令；
- `CONFIRMATION` 结果终结该检测；
- `MANUAL_RECHECK` 是人工重新检测的终态角色。

fixed-frame 不会因角色不同执行新的 MCU 动作。每次调用分别读取该调用时的最近缓存；
如果两次调用之间没有新的合法 `DD/EF`，`CONFIRMATION` 通常会得到与 `INITIAL` 相同的
重量和红外原始位。本文把它明确记录为兼容确认，不描述为独立物理复采。

后端必须按检测当前状态核对角色，不能接受任意组合：

- 投递或清运检测从 `INITIAL` 开始；
- 只有等待确认的同一检测可以接收 `CONFIRMATION`；
- 人工重检使用 `triggerType=MANUAL_RECHECK` 和
  `sampleRole=MANUAL_RECHECK`；
- 已终结检测上的新角色结果不得覆盖原终态。

#### 4.5.6 后端归并语义

后端必须校验可信 OneNet 来源、Schema、摘要、设备部署和原命令，并要求：

- `target.type=FULLNESS_DETECTION`；
- `target.uid == payload.detectionUid`；
- `commandUid` 对应同一检测和角色的 `SAMPLE_FULLNESS`；
- `portNo`、`sampleRole`、`triggerType`、`fullnessMode` 和
  `frozenConfig` 与原检测及命令快照一致；
- fixed-frame `NOT_SAMPLED` 分支严格符合本节的历史 `1/1` 或无历史 `0/0` 形状；
- 总重量测量符合正常兼容质量形状；
- 分母使用检测冻结的正数 `configuredFullWeightGrams`。

同一数据库事务至少完成：

1. 保存可信 edge event 和唯一满溢物理结果；
2. 创建唯一 `(detectionUid, sampleRole)` 样本；
3. 保存总重量、无历史分支和红外原始辅助观测；
4. 按本节公式计算百分比和 `FULL/NOT_FULL`；
5. 推进检测状态，并按需建立唯一确认采样任务；
6. 核对当前投口、袋、规则指纹和 `currentDetection` 代际；
7. 对仍为当前代际的结果更新容量投影并创建、延续或恢复持续满溢事件；
8. 对已经过时代际的可信样本保存 `STALE_IGNORED`，不覆盖新状态；
9. 完成原采样命令和任务；
10. 将可信 inbox 标记为已处理；
11. 创建本 `eventUid` 的唯一 `confirmEdgeEvent` 意图。

后端只有在上述业务结果、必要状态投影和确认意图同事务提交后，才能业务确认本事件。
OneNet/Pulsar 的传输 ACK 不是业务确认。

#### 4.5.7 幂等、重启与可靠发送

业务唯一根是：

```text
(detectionUid, sampleRole)
```

具体规则为：

- 同一 `detectionUid` 可以分别产生一条 `INITIAL` 和一条 `CONFIRMATION`；
- 每个角色最多一条不可变结果；人工重检检测最多一条 `MANUAL_RECHECK`；
- 相同命令重试复用原事件身份、冻结观测和载荷；
- 以新 `commandUid` 重复请求同一检测角色，也不得读取新缓存或创建第二结果；
- 同事件同摘要重投复用原样本和原确认；
- 同事件异摘要、同部署序号异事实或同角色第二份不同结果必须隔离；
- 本地命令结果、冻结观测、事件身份和发件箱必须原子提交；
- 该本地计算不占用全局物理工作槽，进程重启后可从持久命令和冻结观测安全重放；
- MQTT、OneNet 回执丢失、断网或重启后持续重发同一事件；
- 收到匹配 `confirmEdgeEvent` 后才把原事件置为业务已确认。

全局最近观测本身必须跨重启保存。fixed-frame 启动不得主动清空它；只有新的合法
`DD/EF` 才覆盖最近值，损坏缓存应保留诊断信息并按“没有可用历史”的 0 克兼容分支
完成当前命令。

#### 4.5.8 当前实现判断与保留任务

香橙派当前已经能够在同一进程中：

- 缓存最后一条合法 `DD/EF` 的 `POST` 和 `FULL`；
- 对 `INITIAL` 读取该缓存；
- 构造 `DIGITAL_INFRARED / NOT_SAMPLED / null distance / 1/1`；
- 在不写 UART 的情况下创建一条 `FULLNESS_SAMPLE_COMPLETE`。

当前仍有以下端到端阻塞或可靠性缺口：

1. 当前只接受 `sampleRole=INITIAL`，`CONFIRMATION` 和 `MANUAL_RECHECK` 会被
   `MCU_FEATURE_NOT_SUPPORTED` 拒绝。
2. 没有历史或缓存损坏时当前直接失败，尚未构造
   `0 克 / CLEAR / NOT_SAMPLED / 0/0`。
3. 本地计算错误占用全局 `FULLNESS` 工作槽，因此投递或清运进行中可能返回
   `DEVICE_BUSY`。
4. 最近观测虽然写入 SQLite，fixed-frame 启动却会主动清空，现有测试还固化了该错误
   行为。
5. 缓存尚无可靠的观测时间和配置代际，消费时也不使用已经保存的
   `sourceWorkType/sourceWorkUid`；命令结果未冻结所选来源快照。
6. fixed-frame 测量使用 UUIDv5 和 `sampleCount=0`，违反正式 UUIDv4 Schema 和后端
   稳定测量至少一个样本的约束。
7. 事件未以 `(detectionUid, sampleRole)` 建立稳定唯一身份；同检测角色换一个
   `commandUid` 可能读取新缓存并创建第二事件。
8. 命令完成、冻结观测、事件建立和工作槽释放不在一个事务；强杀可能留下已完成命令
   但永久没有结果事件。
9. 运行时尚未专项校验
   `target.uid == payload.detectionUid`；native 结果处理也未核对 MCU 返回的端口、角色
   和传感器字段与原命令一致。
10. OneNet Schema 仅约束 `MEASURED_MEDIAN` 的距离字段，尚未校验
    `valid <= requested`、角色与触发组合、kind/basis/value、fixed-frame `NOT_SAMPLED`
    两种形状和测量质量矩阵；契约语义校验器也没有本事件专项分支。
11. 后端当前只消费 `configurationProgress`。`fullnessSampleComplete` 会被警告后由
    MQ ACK，不进入可信 inbox，也不创建物理结果、样本或容量状态。
12. 现有数据库仍按“总重量减当前皮重”强制保存 baseline、raw net 和百分比，不能按
    本节确认的总重量公式落库；既有需求、UART 和接口文档也尚未同步。
13. `dev_physical_result.fullness_sample_id` 必填并外键指向样本，同时
    `rec_fullness_sample.physical_result_id` 也必填并反向指向物理结果；MySQL 外键不可
    延迟，形成当前无法插入任一侧的必填循环。
14. 当前表没有无损保存 `fullnessSensorKind`、`fullnessSampleBasis`、距离及请求/有效
    计数；事件又没有红外健康字段，现有物理结果表要求后端额外填充
    `infrared_health`。
15. 普通可靠事件发布后进入 `SENDING`，尚无重启恢复和业务确认前持续重发路径。
16. 后端 OneNet 下行目前只支持 `APPLY_CONFIGURATION`，尚不能真正下发
    `SAMPLE_FULLNESS`、`confirmEdgeEvent` 或消费业务确认回执。

后续实施必须同步修改运行优先公式的上游文档和数据库，解除物理结果与样本的循环外键，
再打通三角色、无历史 0 克、跨重启缓存、调用时快照冻结、UUIDv4 合法身份、角色级
幂等、本地原子事件、契约专项校验、后端可信消费、容量状态机和业务确认，并覆盖：

- 三种角色及其合法状态转换；
- 有历史和无历史两种 `NOT_SAMPLED` 形状；
- 重启保留、损坏缓存和强杀恢复；
- 同角色重复命令与第二份不同结果；
- 空袋非零百分比、红外 `BLOCKED` 但重量未满；
- 无历史人工重检解除满溢；
- 过时代际只保存、不覆盖当前投影。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.6 `baselineMeasurementComplete`

状态：**已确认**

#### 4.6.1 什么时候发生

后端创建空袋皮重测量任务并调用 `measureEmptyBagBaseline` 后，fixed-frame 香橙派不向
MCU 发送独立称重命令，而是按以下顺序选择兼容皮重：

1. 当前 `bagUid` 已由最近一次清运 `EF.POST` 建立并可靠保存的皮重；
2. 当前袋没有对应皮重时，选择 SQLite 中最近一次合法 `DD.POST` 或 `EF.POST`；
3. 没有任何合法历史重量时，选择 0 克。

香橙派冻结选中的重量、真实来源类别、测量及事件身份，并在本地可靠保存后创建
`baselineMeasurementComplete`。该事件表示：

> 本次皮重任务已经按 fixed-frame 兼容规则完成，选中值已保存为命令中 `bagUid` 的
> 当前皮重。

它不表示 MCU 在服务调用时执行了新称重，也不表示缓存重量产生于事件时间。

该流程完全在香橙派本地完成，不发送 UART、不等待 MCU ACK 或测量超时，也不占用全局
物理工作槽。没有历史重量不是失败条件；命令非法、摘要或目标不匹配、
`emptyBagConfirmed` 不为 `true`、配置不匹配、重量越界或 SQLite 可靠保存失败才不产生
正常完成事件。

#### 4.6.2 有什么作用

该事件是后端将一次皮重任务应用为当前袋有效皮重的可靠事实，供后端：

1. 保存唯一皮重物理结果；
2. 完成原 `measurementUid` 皮重任务；
3. 为当前 `bagUid` 建立或确定新的皮重版本；
4. 更新投口当前皮重和容量状态；
5. 建立后续 `MANUAL_RECHECK` 满溢检测和必要任务；
6. 为本事件建立 `confirmEdgeEvent`。

服务同步返回 `ACCEPTED` 只表示命令已经可靠受理，不能单独修改后端皮重。后端必须在
可信处理本事件后才应用皮重。

#### 4.6.3 fixed-frame 事件字段

事件身份字段必须来自原命令：

```text
payload.measurementUid    = command.payload.measurementUid
payload.portNo            = command.payload.portNo
payload.bagUid            = command.payload.bagUid
payload.emptyBagConfirmed = true
payload.frozenConfig      = command.payload.config
```

`emptyBagConfirmed=true` 是现场人员通过原命令作出的确认，不是 MCU 传感器事实。香橙派
只允许复制该确认，不得在原命令缺失或为 `false` 时自行构造。

`totalWeightMeasurement` 使用选中的皮重，并固定构造成：

```text
status               = STABLE
weightValueAvailable = true
reportedWeightGrams  = selectedBaselineWeightGrams
weightValueKind      = STABLE_WINDOW_MEAN
sampleCount          = 1
sensorHealth         = OK
faultCode            = null
```

其他质量字段使用合法的正常兼容值。其中：

- 有历史观测时尽可能保留来源 `mcuBootId` 和 `mcuEventSequence`；
- 无历史 0 克时使用可靠保存的正常兼容 MCU 身份；
- 顶层 `payload.measurementUid` 是皮重任务身份；
- 嵌套 `totalWeightMeasurement.measurementUid` 是本次兼容重量事实身份；
- envelope `eventUid` 是可靠事件身份；
- 以上三个身份必须彼此分离并使用持久化 UUIDv4。

香橙派内部同时保存：

```text
SAME_BAG_CLEAN_POST
LATEST_FLOW_POST
NO_HISTORY_ZERO
```

三类兼容来源及对应来源作业、接收身份和时间，供重启恢复与诊断。当前 9 服务 / 13 事件
模型没有来源字段，本轮不修改 OneNet 线上结构；后端结合设备 fixed-frame 兼容配置和
事件形状，将该结果记录为 fixed-frame 兼容建立，不宣称发生了新的 MCU 实测。

#### 4.6.4 三种来源的业务结果

项目负责人确认三种来源全部作为正常成功皮重处理：

- 同袋 `EF.POST` 直接成为该袋当前皮重；
- 最近其他流程 `DD.POST/EF.POST` 即使可能包含垃圾，也成为当前袋皮重；
- 无历史 0 克成为当前袋皮重。

它们都不返回 `SOURCE_FAILED`，也不因为缺少本次 MCU 新采样而把基准标成
`INVALID/UNINITIALIZED`。

第二类回退可能把其他袋或含有垃圾的投递结束总重量保存成空袋皮重，随后影响
`PRE - oldBaselineWeightGrams` 的清运净重。第三类回退会建立 0 克皮重。本文明确将
两者记录为“先跑起来”的已接受 fixed-frame 风险，不把它们描述为真实测量精度已经
解决。

成功建立皮重后，后端创建新的 `MANUAL_RECHECK` 满溢检测。该检测按 4.5 已确认的
fixed-frame 规则，使用调用时最近总重量除以下发满溢总重量；当前皮重不参与满溢
百分比计算。

#### 4.6.5 调用时冻结与本地原子事务

香橙派首次处理命令时必须冻结本次选择的重量和来源。后续即使收到新的 `DD/EF`，同一
`measurementUid` 也不能重新选择。

以下内容必须在一个 SQLite 事务中可靠提交：

1. `bagUid → 当前皮重、来源类别和来源身份`；
2. 原命令的完成结果；
3. 顶层任务、嵌套测量和事件 UUIDv4；
4. 不可变事件载荷及摘要；
5. 可靠发件箱记录。

事务提交失败时不能留下“命令已完成但皮重或事件不存在”的局部状态。重启后从冻结结果
恢复和重发，不读取重启时的新最近重量。

本地皮重保存不等待 MQTT、OneNet 或后端业务确认。后端稍后把事件判为过期，不回滚
香橙派按 `bagUid` 保存的本地兼容皮重；后续命令仍可按明确袋身份使用它。

#### 4.6.6 后端归并语义

后端必须校验可信 OneNet 来源、Schema、摘要、设备部署和原命令，并要求：

- `target.type=BASELINE_MEASUREMENT`；
- `target.uid == payload.measurementUid`；
- `commandUid` 对应同一任务的 `MEASURE_EMPTY_BAG_BASELINE`；
- `portNo`、`bagUid`、`emptyBagConfirmed` 和 `frozenConfig` 与原任务快照一致；
- 兼容测量为非负、稳定、可用的正常形状；
- 顶层任务身份、嵌套测量身份和事件身份均为不同 UUIDv4。

如果事件到达时：

- `bagUid` 仍是该投口当前袋；
- 原皮重任务仍处于可应用状态；
- 配置、容量版本和投口代际仍与任务快照一致；

则后端在一个事务内：

1. 保存可信 edge event 和唯一皮重物理结果；
2. 完成唯一 `measurementUid` 皮重任务；
3. 创建或确定该袋的新当前皮重版本；
4. 把投口容量状态的当前皮重更新为该值；
5. 创建新的 `MANUAL_RECHECK` 满溢检测及任务；
6. 将可信 inbox 标记为已处理；
7. 创建本 `eventUid` 的唯一 `confirmEdgeEvent` 意图。

如果当前袋已经更换，或配置、容量、投口代际已经变化，则后端仍保存可信事件和物理
证据，把任务处置为 `STALE_IGNORED`，不得用旧事件覆盖新袋或新基准。安全保存过期
事实后可以返回 `NO_ACTION_REQUIRED` 业务确认。

后端现有 `rec_port_weight_baseline.source_type` 只有
`INITIAL_BINDING/CLEAN_COMPLETE/MANUAL_REMEASUREMENT`。实施需要增加明确的
fixed-frame 兼容来源，或建立等价的可审计表达；禁止把历史复用或 0 克默认值伪记成
真实 `MANUAL_REMEASUREMENT`。

#### 4.6.7 幂等、重启与可靠确认

- `measurementUid` 是本任务和结果的业务唯一根；
- 一个 `measurementUid` 最多对应一个冻结皮重、一个物理结果和一个完成事件；
- 相同 `commandUid` 和相同正文重试复用原结果；
- 使用新 `commandUid` 重复请求同一 `measurementUid`，也不得重新选择重量或创建第二
  结果；
- 同事件同摘要重投复用原皮重结果和确认；
- 同事件异摘要、同部署序号异事实或同任务第二份不同结果必须隔离；
- 香橙派持续重发同一 `eventUid` 和不可变载荷；
- 后端只有在皮重任务终态、新基准或过期处置、必要后续检测任务和确认意图同事务提交
  后，才能业务确认本事件；
- 香橙派收到匹配 `confirmEdgeEvent` 后才停止原事件重传。

#### 4.6.8 当前实现判断与保留任务

当前保留的 `uart-v1` 路径能够：

- 占用 `BASELINE` 工作槽并发送 `MEASURE_BASELINE`；
- 接收匹配 `measurementUid/mcuCommandUid` 的
  `BASELINE_MEASUREMENT_RESULT`；
- 构造 `BASELINE_MEASUREMENT_COMPLETE` 并释放工作槽。

当前 fixed-frame 和端到端闭环仍有以下缺口：

1. fixed-frame 直接返回 `MCU_FEATURE_NOT_SUPPORTED`，尚未实现本地皮重选择和成功
   事件。
2. SQLite 没有完整的 `bagUid → 当前皮重、来源类别和来源身份` 持久化能力；清运
   `EF.POST` 也没有形成可按袋查询的当前皮重。
3. 共用的 fixed-frame 兼容测量工具当前生成 UUIDv5 和 `sampleCount=0`；正式 Schema
   和后端稳定测量要求 UUIDv4 及至少一个样本。
4. 无历史 0 克所需的持久化正常 MCU 兼容身份尚未实现。
5. fixed-frame 启动当前会清空最近 `DD/EF` 观测，违反跨重启回退要求。
6. 命令完成、皮重保存、事件建立和发件箱尚无一个原子事务及强杀恢复路径。
7. 运行时尚未专项校验
   `target.uid == payload.measurementUid`；保留的 native 结果处理也只核对任务和
   `mcuCommandUid`，未核对端口及完整测量语义。
8. OneNet Schema 只有字段形状，契约语义校验器没有本事件专项分支，尚未强制正常
   fixed-frame 测量、非负重量、不同 UUIDv4 身份和命令快照一致。
9. native 测量路径先完成命令、再创建事件、最后释放槽，强杀可能留下命令完成但事件
   不存在的局部状态。
10. 后端当前只消费 `configurationProgress`。本事件会被警告后由 MQ ACK，不进入可信
    inbox，也不会完成皮重任务、建立基准或创建后续检测。
11. 后端数据库可以表达正常皮重任务和基准，但来源枚举只能称为
    `MANUAL_REMEASUREMENT`，无法准确记录 fixed-frame 历史值或 0 克兼容来源。
12. 后端 OneNet 下行目前只支持 `APPLY_CONFIGURATION`，尚不能真正下发
    `MEASURE_EMPTY_BAG_BASELINE`、`confirmEdgeEvent` 或消费业务确认回执。
13. 普通可靠事件发布后进入 `SENDING`，尚无重启恢复和业务确认前持续重发路径。

后续实施必须打通按袋皮重存储、三层 UUIDv4 身份、三种来源冻结、0 克兼容、原子本地
完成、契约专项校验、后端可信消费、兼容来源数据库表达、新基准与
`MANUAL_RECHECK` 创建及业务确认，并覆盖：

- 同袋 `EF.POST`、最近其他流程重量和无历史 0 克三条成功路径；
- 当前袋或代际变化时 `STALE_IGNORED`；
- 同任务不同命令、同事件不同摘要和第二物理结果冲突；
- 重启、损坏缓存和进程强杀；
- 后端成功建立新基准及过期结果不覆盖新袋。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.7 `deviceFaultObserved`

状态：**已确认**

#### 4.7.1 什么时候发生

当香橙派首次明确观测到一个持续存在的设备或组件故障，或者一个尚未恢复的活动故障
严重度升级时，产生 `deviceFaultObserved`。

它不是每次命令失败的通用事件：

- 命令被拒绝、UART 单次写入失败、`DD/EF` 等待超时等单次命令结果，优先由
  `deviceCommandObserved` 表达；
- 只有香橙派能够直接确认故障已经持续存在、影响范围超出单次命令，才建立活动故障并
  产生本事件；
- 同一活动故障在相同严重度下被重复发现时，不重复创建可靠事件，只更新本地发现次数
  和最后发现时间；
- 同一活动故障严重度升级时，保留原 `faultUid`，使用新的 `eventUid` 再产生一次
  `deviceFaultObserved`。

心跳恢复、偶发一次成功或缺少新的错误都不表示故障已经恢复。本事件只负责建立或升级
活动故障，具体关闭语义由下一项 `deviceFaultRecovered` 处理。

#### 4.7.2 有什么作用

该事件用于把香橙派能够明确观测的持续故障可靠送达后端，供后端：

1. 建立或更新一个正在持续的设备故障；
2. 更新设备运行状态投影；
3. 创建运维告警；
4. 按严重度阻断相应投口或整台设备的新业务；
5. 为本事件建立 `confirmEdgeEvent`。

本事件不是 MCU 全量健康遥测，也不证明未列出的组件正常。后端不能仅凭心跳或某一次
命令成功自动关闭故障，必须等待匹配同一 `faultUid` 的可信恢复事件。

#### 4.7.3 fixed-frame 可观测边界

冻结的 fixed-frame MCU 只提供既定的命令帧和 `DD/EF` 重量帧，没有故障帧、健康
查询、ACK、门状态、传感器状态或恢复帧。因此香橙派不得伪造以下 MCU 内部或下游组件
故障：

- `DELIVERY_DOOR`
- `CLEAN_SOLENOID`
- `WEIGHT_SENSOR`
- `FULLNESS_SENSOR`
- `SMOKE_SENSOR`
- `MCU_STORAGE`
- `MCU_INTERNAL`

也不得根据以下现象推断上述故障：

- `DD/EF` 等待超时；
- 重量值看起来异常；
- 没有收到 ACK；
- 红外 `FULL` 位变化；
- 香橙派重启；
- 无法取得 MCU 当前状态。

在本项目“先跑起来”的 fixed-frame 兼容前提下，无法取得 MCU 额外状态属于已知协议
限制，按正常兼容状态处理，本身不是故障事实。

当前只允许上报香橙派能够直接观测的以下故障：

| 直接观测到的持续故障 | `component / faultCode` | 初始严重度与处理 |
| --- | --- | --- |
| UART 无法打开、连接关闭、持续读写错误或短写 | `UART / UART_PROTOCOL` | `BLOCK_DEVICE` |
| 连续不可解析的 fixed-frame 候选达到诊断阈值 | `UART / UART_PROTOCOL` | 初始 `WARNING`，持续恶化可升级 |
| SQLite 完整性失败或持续关键存储失败 | `EDGE_STORAGE / EDGE_STORAGE` | `BLOCK_DEVICE` |
| 摄像头持续无法打开、拍摄或保存本地照片 | `CAMERA / CAMERA_CAPTURE` 或 `CAMERA_STORAGE` | `WARNING` |
| 明确检测到网络持续不可用 | `NETWORK / NETWORK_CONNECTIVITY` | `WARNING`，本地保存，网络恢复后补发 |
| 明确检测到系统时钟持续未同步 | `CLOCK / CLOCK_UNSYNCED` | `WARNING` |

单个噪声字节不能建立 UART 故障；单次拍照失败只记录本次照片缺失或照片状态，不建立
设备故障，也不阻止发送开门命令。

SQLite 完全不可写时无法声称故障事件已经可靠持久化。此时先输出本机诊断日志；存储
恢复后，允许根据可审计的本地诊断事实补建并发送该故障，但必须保留实际首次发现时间
和补录标记，不能伪称故障发生当时已经可靠入库。

#### 4.7.4 事件字段与作用域

本事件固定使用设备部署作为目标：

```text
target.type = DEVICE_DEPLOYMENT
target.uid  = deploymentCode
commandUid  = null
```

载荷必须包含：

```text
faultUid
portNo
component
severity
faultCode
mcuBootId
mcuEventSequence
```

字段规则如下：

- `faultUid` 是一次连续故障期间不变的持久化 UUIDv4；
- 设备级故障使用 `portNo=null`，能够明确限定到某个投口时才填写对应 `portNo`；
- 当前 fixed-frame 香橙派自身观测的故障使用
  `mcuBootId=null`、`mcuEventSequence=null`，两者必须同时为空；
- 未来只有真正来自 `uart-v1` MCU 故障事件时，才填写 MCU 提供并可靠保存的真实
  `mcuBootId` 和 `mcuEventSequence`；
- envelope `eventUid` 是本次可靠事件身份，与 `faultUid` 分离，并使用持久化
  UUIDv4；
- `faultCode` 使用中心化、稳定的符号代码，不得直接上传异常消息、原始串口内容、路径、
  密钥或其他敏感文本；
- 没有真实 `deploymentCode` 时不得使用 `Dp_unknown` 等占位身份发送可信故障事件。

#### 4.7.5 持续故障、去重与严重度升级

香橙派以以下四元组作为活动故障键：

```text
(deploymentCode, portNo, component, faultCode)
```

生命周期规则为：

1. 活动键不存在时，创建新的 UUIDv4 `faultUid`，保存首次发现信息并产生首个事件；
2. 同一活动键、同一严重度再次被发现时，只更新本地 `discoveryCount` 和
   `lastDetectedAt`，不创建新事件；
3. 同一活动故障严重度升级时，沿用 `faultUid`，使用新 `eventUid` 产生升级事件；
4. 允许的升级方向为
   `WARNING → BLOCK_PORT`、`WARNING → BLOCK_DEVICE` 和
   `BLOCK_PORT → BLOCK_DEVICE`；
5. 观测到影响变轻不等于恢复，不产生降级事件，也不得降低后端已有阻断；
6. 同一事件重试必须复用原 `eventUid`、原载荷和原摘要；
7. 活动故障关闭只由后续匹配同一 `faultUid` 的 `deviceFaultRecovered` 完成。

故障记录、发现计数、严重度升级和可靠事件发件箱必须在同一个 SQLite 事务中提交。
不得出现活动故障已经建立或升级，但对应可靠事件丢失的局部状态。

#### 4.7.6 后端归并与阻断语义

后端必须校验可信 OneNet 来源、Schema、部署目标、端口作用域、组件与故障码关系、
严重度以及 MCU 身份成对为空或成对存在。可信事件按以下规则归并：

- `faultUid` 标识同一次连续故障；
- 活动故障键防止同一故障被重复建立；
- 同事件同摘要重投只返回已有处理结果和确认；
- 同事件异摘要、同活动键出现不同活动 `faultUid` 或不合法严重度变化必须隔离；
- 严重度只能单调升级，迟到的较轻事件不能覆盖已保存的较重严重度。

严重度映射固定为：

| OneNet 严重度 | 后端影响级别 | 业务动作 |
| --- | --- | --- |
| `WARNING` | `DEGRADED` | 创建运维告警，不阻断投递和清运 |
| `BLOCK_PORT` | `BUSINESS_BLOCKING` | 只阻断对应投口的新业务 |
| `BLOCK_DEVICE` | `SAFETY_BLOCKING` | 阻断整台设备的新业务 |

后端在一个事务中完成：

1. 保存故障事件和活动故障；
2. 更新运行状态投影及单调严重度；
3. 建立对应投口或整机阻断；
4. 创建运维告警意图；
5. 将可信 inbox 标记为已处理；
6. 创建本 `eventUid` 的唯一 `confirmEdgeEvent` 意图。

在当前 fixed-frame 范围内，UART 无法可靠使用和香橙派关键存储无法可靠使用会实际
阻止业务闭环，因此使用 `BLOCK_DEVICE`。摄像头、网络和时钟故障按 `WARNING`
处理，以满足“先跑起来”；它们仅告警，不阻断投递或清运。

#### 4.7.7 本地重试、恢复与业务确认

- 香橙派先在本地原子提交活动故障和事件，再尝试 MQTT 发布；
- 断网时保留事件，网络恢复后继续发送同一 `eventUid` 和不可变载荷；
- MQTT/OneNet 投递成功不等于后端业务处理成功；
- 后端只有在故障、状态投影、阻断、告警意图和确认意图同事务提交后，才能业务确认；
- 香橙派收到匹配 `confirmEdgeEvent` 后才停止该事件的可靠重传；
- 进程重启后必须恢复 `PENDING/SENDING` 事件，不能永久停留在 `SENDING`；
- 一次命令后来成功、心跳恢复或没有继续报错，都不能在本地自动清除活动故障。

#### 4.7.8 当前实现判断与保留任务

当前实现尚不能完成本事件闭环：

1. fixed-frame MCU 没有 `FAULT_OBSERVED` 帧，香橙派也没有针对上述直接可观测故障的
   完整事件生成器。
2. 当前本地 `record_fault()` 只写 `faults` 表，不会自动创建 OneNet 事件。
3. 保留的 `uart-v1` `_on_fault_observed` 先写故障、再另行创建事件，两者不是同一个
   原子事务；进程在中间退出会留下局部状态。
4. 同一 MCU 事件在崩溃后重新处理可能生成新的随机 `eventUid`，无法保证重试身份
   稳定。
5. 当前没有按活动故障键维护重复发现次数、最后发现时间和严重度升级的完整状态机。
6. fixed-frame 解析器会静默丢弃无效数据，尚无连续不可解析候选的诊断计数和阈值。
7. 本地故障存储仍使用数字故障码，云端契约要求稳定符号代码，需要中心化映射。
8. 后端 OneNet 分发当前只处理 `configurationProgress`；本事件会被警告后由 MQ ACK
   丢弃，不会进入可信处理、状态投影、告警或阻断流程。
9. 契约语义校验器已有组件与故障码关系及 MCU 身份成对规则，但还未完整约束端口
   作用域、严重度关系和跨事件单调升级。
10. 后端已有 `dev_device_fault_event`、活动故障键、影响级别和状态字段，能够表达本
    策略，但可信消费者尚未实现。
11. 普通可靠事件发布后可能停留在 `SENDING`，尚无业务确认前的完整重启恢复和持续
    重发路径。
12. 后端尚未打通 `confirmEdgeEvent` 下行和确认回执。

后续实施必须覆盖：

- 首次发现、相同严重度重复发现和严重度升级；
- MCU 状态缺失、`DD/EF` 超时和单次命令失败不推断 MCU 组件故障；
- 故障与事件同事务提交及进程强杀恢复；
- 设备级与投口级作用域；
- fixed-frame 事件 MCU 身份成对为空；
- 非法组件与故障码组合、非法端口和非法严重度变化；
- 存储不可写后恢复时的可审计补录；
- 后端幂等归并及迟到较轻事件不能降级；
- 业务确认前重发和重启后恢复。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.8 `deviceFaultRecovered`

状态：**已确认**

#### 4.8.1 什么时候发生

当香橙派对一个已经存在的活动故障取得明确的、与故障种类相对应的恢复证据时，产生
`deviceFaultRecovered`。它只关闭载荷中指定的同一 `faultUid`，不表示整台设备所有
组件都正常。

以下现象不能单独作为恢复证据：

- 最近一段时间没有再次报错；
- 收到普通心跳；
- 香橙派进程或整机重启；
- 一次与故障组件无关的命令成功；
- fixed-frame MCU 没有提供当前状态；
- 人为假定 MCU 或未知组件已经恢复。

fixed-frame MCU 没有恢复帧，因此当前只能恢复 4.7 中由香橙派直接观测并建立的故障。
香橙派不得为从未真实观测的门、清运电磁阀、重量、满溢、烟感、MCU 存储或 MCU 内部
故障伪造恢复事件。未来保留的 `uart-v1` MCU 可以用真实 `RECOVERED` 生命周期帧关闭
它先前提供的同一 `faultUid`。

#### 4.8.2 有什么作用

该事件是某一个持续故障已经结束的可靠事实，供后端：

1. 将同一 `faultUid` 标记为 `RECOVERED`；
2. 记录恢复来源和恢复时间；
3. 关闭该故障对应的运维告警；
4. 清除该故障造成的投口或整机阻断；
5. 重新计算设备和投口的运行状态；
6. 为本事件建立 `confirmEdgeEvent`。

如果同一设备仍有其他活动故障，则对应告警和阻断继续保留。恢复一个
`BLOCK_DEVICE` 故障也不能覆盖另一项仍然活动的整机阻断。

#### 4.8.3 fixed-frame 恢复证据

香橙派按具体故障原因使用最小但明确的正向证据：

| 活动故障 | 产生恢复事件的条件 |
| --- | --- |
| UART 无法打开或连接关闭 | 同一串口成功重新打开 |
| UART 持续写入错误或短写 | 下一次完整 fixed-frame 命令帧全部写入成功 |
| UART 持续读取错误 | 再次成功读取并解析一个合法 `DD/EF` 完整帧 |
| UART 连续协议错误达到阈值 | 收到一个合法完整帧，并把协议错误连续计数清零 |
| SQLite 完整性或关键存储故障 | 完整性检查通过，并成功提交一次真实测试事务 |
| 摄像头持续打开、拍摄或本地保存故障 | 成功完成一次对应的打开、拍摄和本地保存 |
| 网络持续不可用 | MQTT 成功重新连接 OneNet |
| 系统时钟持续未同步 | 操作系统明确报告时间同步已经恢复 |

如果同一 `(deploymentCode, portNo, component, faultCode)` 活动故障内部合并记录了多个
本地原因，则所有仍然活动的原因都取得对应恢复证据后才关闭该 `faultUid`。例如 UART
重新打开不能关闭仍在持续的协议解析故障。

摄像头恢复不要求照片已经上传 COS；网络上传属于不同组件。下一次业务照片成功即可
作为恢复证据，不要求为恢复专门阻塞业务或强制拍摄测试照片。

#### 4.8.4 事件字段

恢复事件固定使用：

```text
target.type = DEVICE_DEPLOYMENT
target.uid  = deploymentCode
commandUid  = null
```

载荷与观察事件使用同一结构，并遵循以下规则：

- `faultUid` 必须复用要关闭的原活动故障 UUIDv4；
- `component`、`faultCode` 和 `portNo` 必须与原故障一致；
- `severity` 填本轮连续故障生命周期达到过的最高严重度，不能因为故障已经恢复而降为
  `WARNING`；
- 当前 fixed-frame 香橙派自身故障继续使用
  `mcuBootId=null`、`mcuEventSequence=null`，两者同时为空；
- 未来真实 MCU 恢复帧使用该恢复帧自己的真实 MCU 事件身份，不要求与观察帧序号相同；
- envelope `eventUid` 是本次恢复事实的新 UUIDv4，与 `faultUid` 分离；
- 同一恢复事件重试必须复用原 `eventUid`、载荷和摘要；
- 没有真实 `deploymentCode` 时不得使用占位部署身份发送可信恢复事件。

#### 4.8.5 本地关闭、新故障与原子事务

香橙派取得明确恢复证据后，在一个 SQLite 事务中：

1. 校验该 `faultUid` 仍是同一活动故障；
2. 保存恢复证据类别和实际恢复时间；
3. 将活动故障标记为 `RECOVERED`；
4. 生成新的持久化 `eventUid` 和不可变恢复载荷；
5. 创建可靠事件发件箱记录。

事务失败时不能留下“本地已经恢复但恢复事件不存在”或相反的局部状态。MQTT、
OneNet 或后端暂时不可用不影响本地恢复事实；香橙派稍后持续重发相同事件。

故障恢复后，相同活动故障键再次出现时，必须创建新的 `faultUid`，视为下一轮独立故障，
不能重新打开已经恢复的旧 `faultUid`。

一次业务命令成功只在它恰好构成该故障的明确恢复证据时使用。例如完整 UART 写入成功
可以恢复“持续写入或短写”原因，但不能恢复摄像头、SQLite 或 UART 读取故障。

#### 4.8.6 网络和存储故障的补发

网络故障期间，`deviceFaultObserved` 保存在本地发件箱中。MQTT 重新连接成功后：

1. 将该网络故障本地标记为恢复；
2. 创建同一 `faultUid` 的 `deviceFaultRecovered`；
3. 先发送观察事件，再发送恢复事件；
4. 两个事件分别等待后端业务确认。

SQLite 完全不可写时，观察事件无法在故障发生时可靠保存。存储恢复后，香橙派在一次
可靠事务中补建：

1. 原 `deviceFaultObserved`，保留可审计的实际首次发现时间和补录标记；
2. 同一 `faultUid` 的 `deviceFaultRecovered`，记录实际恢复时间；
3. 两个不同、持久化的 `eventUid`；
4. 按观察在前、恢复在后的发件箱顺序。

不得只发送恢复事件，也不得把补录时刻伪装成故障最初发生时刻。线上 9 服务 / 13 事件
载荷当前没有补录字段，因此补录证据和原始诊断时间先保存在香橙派本地；后端结合
`occurredAt`、`clockQuality` 和事件序号审计，后续实施时再决定是否扩展契约。

#### 4.8.7 后端归并、乱序与自动解除阻断

后端必须按 `faultUid` 精确关闭故障，并校验部署、端口、组件、故障码、最高严重度和
MCU 身份规则：

- 同事件同摘要重投只复用已有恢复结果和确认；
- 同事件异摘要必须隔离；
- 恢复事件字段与原故障不匹配时必须隔离，不能按组件名称模糊关闭其他活动故障；
- 未找到原观察事件时，保存为“恢复先到”的可信乱序证据，但不能解除其他
  `faultUid` 的阻断；
- 同一 `faultUid` 的观察事件随后迟到时，后端将两者归并为已经恢复，不能重新打开
  故障；
- 已恢复故障收到重复恢复事件时保持终态，不重复关闭告警或重复执行解除动作；
- 新 `faultUid` 到达时才建立同类故障的新一轮生命周期。

后端在一个事务中：

1. 保存可信恢复事件；
2. 将同一故障更新为 `RECOVERED`；
3. 关闭该故障告警；
4. 删除或失效仅由该故障建立的阻断；
5. 根据其他活动故障重新计算设备和投口状态；
6. 将可信 inbox 标记为已处理；
7. 创建本 `eventUid` 的唯一 `confirmEdgeEvent` 意图。

项目负责人确认以“先跑起来”为优先：包括 `BLOCK_DEVICE` 在内，只要香橙派取得本节
规定的明确恢复证据，后端就自动解除该故障造成的阻断，不等待工作人员确认。此规则
只关闭精确匹配的故障，不是忽略其他活动故障。

当前数据库约束要求 `SAFETY_BLOCKING` 故障恢复时
`recovered_by_staff_account_id` 非空，与本决策冲突。实施时必须允许可信
`EDGE_EVENT` 或 `SYSTEM_VERIFIED` 自动恢复整机阻断，并保留恢复来源和证据；不得伪造
工作人员账号来绕过约束。

#### 4.8.8 可靠确认与重启

- 香橙派重启后恢复未确认的恢复事件并继续发送；
- 普通事件发布状态不能永久停留在 `SENDING`；
- 后端只有在故障终态、告警、阻断、状态投影和确认意图同事务提交后，才能业务确认；
- 香橙派收到匹配 `confirmEdgeEvent` 后才停止恢复事件重传；
- 观察和恢复都未确认时优先按 `edgeEventSequence` 发送，后端仍必须容忍 OneNet/MQ
  造成的乱序；
- 恢复事件发布或确认失败不能把本地故障重新标成活动状态；
- 新一轮故障可以在旧恢复事件尚未确认时建立，但必须使用新的 `faultUid` 和更后的
  `edgeEventSequence`。

#### 4.8.9 当前实现判断与保留任务

当前实现尚不能完成本事件闭环：

1. fixed-frame MCU 不提供恢复帧，香橙派尚未实现本节各组件的明确恢复检测器。
2. `mark_fault_recovered()` 只更新本地故障，不创建可靠恢复事件。
3. 保留的 `uart-v1` `_on_fault_observed` 能按 `lifecycle=RECOVERED` 构造恢复事件，
   但故障更新和事件创建分属两个事务。
4. 当前 generic 处理每次都会新建随机 `eventUid`，崩溃重放时无法保证恢复事件身份
   稳定。
5. 当前未校验恢复事件必须匹配仍然存在的同一 `faultUid`、组件、故障码和端口。
6. 当前没有持久化最高严重度、局部原因集合和组件特定恢复证据。
7. 当前无法原子补建 SQLite 故障的观察与恢复事件。
8. 后端 OneNet 分发仍只处理 `configurationProgress`，恢复事件会被警告后由 MQ ACK
   丢弃，不会关闭故障、告警或阻断。
9. 后端已有故障恢复来源、恢复方法和恢复时间字段，但 `SAFETY_BLOCKING` 的人工恢复
   约束不允许本决策中的可信自动恢复。
10. 契约语义校验器只共享校验组件与故障码及 MCU 身份，还未校验恢复事件与原故障的
    跨事件一致性、最高严重度和精确关闭。
11. 后端尚未实现恢复先到、观察迟到时保持终态的乱序归并。
12. `confirmEdgeEvent` 下行、确认回执及业务确认前持续重发仍未打通。

后续实施必须覆盖：

- 每类 fixed-frame 香橙派故障的正向恢复证据；
- 心跳、重启、静默和无关命令成功不能恢复；
- 同一 `faultUid` 精确关闭及字段不匹配隔离；
- 最高严重度保持不变；
- 本地故障关闭与恢复事件同事务提交及强杀恢复；
- 网络故障观察与恢复的断网补发；
- SQLite 恢复后观察与恢复双事件补录；
- 恢复先到、观察迟到和重复恢复的幂等归并；
- 自动解除 `BLOCK_DEVICE` 与其他活动阻断继续保留；
- 恢复后复发使用新 `faultUid`；
- 后端事务、可靠确认和重启重传。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.9 `safetySensorStateChanged`

状态：**已确认**

#### 4.9.1 什么时候发生

该事件表示烟雾传感器的实际状态或健康状态发生了变化，包括：

- `NORMAL → ALARM`；
- `ALARM → NORMAL`；
- `OK → SENSOR_FAULT/DISCONNECTED/...`；
- 非正常健康状态重新恢复为 `OK`。

正常启动时，如果真实 MCU 首次采样为 `NORMAL/OK`，该初始状态进入运行快照即可，不必
产生“变化事件”。如果真实 MCU 启动时已经处于 `ALARM` 或传感器不健康，则必须立即
产生事件，避免只能等待下一次状态变化才能发现异常。

本事件只适用于真实观测到的安全传感器状态。心跳、重启、没有收到新状态或 fixed-frame
链路缺少烟感字段，都不是状态变化事件。

#### 4.9.2 有什么作用

后端使用该事件更新对应投口或整台设备的烟雾状态、烟感健康状态和安全投影：

1. 真实 `ALARM` 建立对应范围的安全阻断和运维告警；
2. 恢复为 `NORMAL/OK` 时清除这一项烟雾阻断；
3. 传感器不健康时标记传感器不可用，并阻断对应范围的新业务；
4. 保存事件发生时的作业上下文；
5. 为本事件建立 `confirmEdgeEvent`。

它不是 `deviceFaultObserved` 的替代物。`ALARM` 表示检测到烟雾，不表示烟感硬件损坏；
传感器不健康可以由本事件更新实时安全状态，只有 MCU 另外提供带 `faultUid` 的真实
故障生命周期事件时，才同时建立 `deviceFaultObserved`。

#### 4.9.3 fixed-frame 兼容策略

冻结的 fixed-frame MCU 没有烟雾状态、烟感健康状态、MCU 启动身份或事件序号，香橙派
无法观测烟雾状态变化。为满足当前“先跑起来”的首要目标，项目负责人确认：

```text
smokeState        = NORMAL
smokeSensorHealth = OK
safetyStatus      = SAFE
```

以上值作为 fixed-frame 兼容运行投影，写入香橙派本地运行状态，并在后续
`deviceRuntimeSnapshot` 中持续使用。不能因为一直收不到烟感状态而把它们改回
`UNKNOWN`，否则后端会永久阻止设备进入业务运行状态。

同时必须保持以下边界：

- 不在 fixed-frame 启动时创建 `safetySensorStateChanged`；
- 不周期性发送虚假的 `NORMAL` 变化事件；
- 不伪造 `mcuBootId` 和 `mcuEventSequence` 冒充 MCU 烟感事件；
- 不从 `DD/EF`、红外 `FULL` 位、命令超时或 MCU 状态缺失推断烟雾状态；
- 当前 fixed-frame 模式下，本事件正常情况下永远不会产生。

即：为了运行而把不可观测状态投影为正常，但不声称实际观测到了一次安全状态变化。

#### 4.9.4 真实 UART 事件字段

未来支持 `SAFETY_SENSOR_EVENT` 的真实 `uart-v1` MCU 使用以下合法组合：

| `smokeState` | `smokeSensorHealth` | `faultCode` |
| --- | --- | --- |
| `NORMAL` | `OK` | `null` |
| `ALARM` | `OK` | `null` |
| `UNKNOWN` | 任一非 `OK` 健康状态 | `SMOKE_SENSOR` |

其他字段规则为：

- `target.type=DEVICE_DEPLOYMENT`；
- `target.uid=deploymentCode`；
- `commandUid=null`；
- `portNo` 有值时表示对应投口，`null` 表示整台设备；
- `workType/workUid` 是状态变化发生时的作业上下文；
- 没有活动作业时必须为 `workType=NONE`、`workUid=null`；
- 有活动作业时 `workUid` 使用真实 UUIDv4，但后端消费时不要求该作业仍处于活动状态；
- `mcuBootId/mcuEventSequence` 必须使用真实 MCU 事件身份且都为正数；
- envelope `eventUid` 使用持久化 UUIDv4，同一 MCU 事件重放时保持不变；
- 没有真实部署身份时不得使用 `Dp_unknown` 等占位值发送可信事件。

`ALARM/OK` 的 `faultCode` 必须为空，因为报警是有效传感器检测结果，不是传感器故障。
传感器不健康时状态必须是 `UNKNOWN`，不能同时宣称一个可信的 `NORMAL` 或 `ALARM`。

#### 4.9.5 香橙派处理和本地原子性

真实 UART 事件到达时，香橙派必须先按
`(mcuReceiveGeneration, mcuBootId, mcuEventSequence)` 去重并校验状态组合，再在一个
SQLite 事务中：

1. 更新相应投口或设备级最后烟雾状态；
2. 更新烟感健康状态；
3. 保存发生时的 `workType/workUid` 上下文；
4. 分配稳定 `eventUid` 和 `edgeEventSequence`；
5. 建立不可变事件载荷、摘要和可靠发件箱记录；
6. 将 MCU inbox 标记为已处理。

进程在任意步骤被强杀后，不能出现状态已经更新但可靠事件不存在，或同一 MCU 事件重放
生成另一个 `eventUid`。MQTT、OneNet 或后端不可用时，本地状态和事件仍可靠保存并在
重启后继续重发。

#### 4.9.6 后端状态、阻断与作业语义

可信事件按作用域处理：

- `portNo` 有值时，只更新和阻断该投口；
- `portNo=null` 时，更新部署级安全状态并阻断整台设备；
- 任一真实 `ALARM/OK` 将对应范围设为 `SAFETY_BLOCKED`；
- `UNKNOWN/非 OK/SMOKE_SENSOR` 将对应范围设为 `OPERATION_BLOCKED`；
- `NORMAL/OK` 清除同一范围的烟雾报警和烟感健康阻断；
- 清除后仍有其他安全事件或活动故障时，相关阻断继续保留。

后端必须阻止新的投递和清运进入被阻断范围。事件中如果带有 `workUid`，只记录报警或
传感器异常发生在哪个作业期间，不能凭该事件伪造 `deliveryComplete`、`cleanComplete`
或作业失败。

真实 MCU 的即时安全动作必须由 MCU 本地状态机负责，不能依赖事件经过
香橙派、OneNet 和后端后再下发。后端也不能把本事件当成“门已经安全关闭”的证据。
正在执行的作业由后续真实 MCU 结果收敛；本事件只阻止新的业务并保存安全事实。

当前数据库同时有部署级和投口级 `safety_status`。实施时必须避免把某一投口的报警无
条件提升为全设备阻断；只有 `portNo=null` 的设备级事件或明确的聚合策略才能设置部署级
阻断。

#### 4.9.7 幂等、顺序和业务确认

- 同一 `eventUid`、相同摘要重投只复用已有状态变更和确认；
- 同事件异摘要或同 MCU 身份异事实必须隔离；
- 后端按部署、作用域和 MCU 事件身份维护状态顺序；
- 迟到的旧 `ALARM` 不能覆盖已经处理的新 `NORMAL`，反之亦然；
- 迟到事件仍保存为历史证据，但不更新当前状态；
- `workUid` 已结束或不存在不影响安全事实本身的可信保存；
- 后端在状态投影、阻断、告警、可信 inbox 和确认意图同事务提交后才能业务确认；
- 香橙派收到匹配 `confirmEdgeEvent` 后才停止原事件重传。

#### 4.9.8 当前实现判断与保留任务

当前实现尚不能完成本事件闭环：

1. fixed-frame MCU 没有烟雾帧，当前适配器不会产生本事件。
2. fixed-frame 现有运行快照仍使用 `UNKNOWN` 占位，与本次已确认的
   `NORMAL/OK/SAFE` 兼容投影冲突。
3. 真实 UART `_on_safety_sensor_event` 会分多次写本地状态，再单独创建事件，不是一个
   原子事务。
4. 当前每次处理都会生成新的随机 `eventUid`，崩溃重放无法保持身份稳定。
5. 当前处理接受默认 `UNKNOWN` 和默认字段，未在落库前完整强制三种合法状态组合。
6. 当前测试明确验证 `ALARM` 后仍允许新投递，与本决策的真实报警阻断语义冲突。
7. 当前只把烟雾状态保存为通用 `device_state`，没有完整的分作用域状态版本和乱序保护。
8. OneNet Schema 要求真实、非空的 MCU 身份，因此 fixed-frame 不能合法伪造本事件；
   这与本次“不发伪事件”的决策一致。
9. UART 契约校验器已有健康状态与烟雾状态组合规则，但 OneNet 语义校验器尚无同等专项
   校验。
10. 后端 OneNet 分发仍只处理 `configurationProgress`，本事件会被警告后由 MQ ACK
    丢弃，不会更新烟感、安全投影、告警或阻断。
11. 后端现有部署级基础阻断可能把投口级安全事件扩大为整机阻断，需实现明确的作用域
    归并。
12. 后端尚未实现按 MCU 顺序拒绝迟到状态覆盖、可靠确认和确认回执。

后续实施必须覆盖：

- fixed-frame 启动、重启和长期运行均投影为 `NORMAL/OK/SAFE`；
- fixed-frame 不创建虚假安全变化事件；
- 真实 `NORMAL`、`ALARM` 和传感器故障三类合法组合；
- 启动时真实异常立即上报，正常启动只进入快照；
- 本地状态、MCU inbox 和可靠事件原子提交及强杀恢复；
- 投口级与设备级作用域；
- 真实报警或传感器故障阻断新业务，恢复正常只清除对应阻断；
- 事件关联作业但不伪造作业结果；
- 迟到事件不覆盖新状态；
- 后端事务、告警、阻断、可靠确认和重启重传。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.10 `photoStatusReported`

状态：**已确认**

#### 4.10.1 什么时候发生

当投递或清运的一个固定照片槽达到终态时，产生 `photoStatusReported`。当前契约只允许
两个终态：

- `AVAILABLE`：照片已成功上传到可信 COS，并取得可验证的正式 URL；
- `PERMANENTLY_MISSING`：已经确定该槽照片无法取得。

`UPLOAD_PENDING` 是拍摄、授权或上传过程中的中间状态，只出现在
`deliveryComplete/cleanComplete` 的照片快照中，不产生本事件。

项目负责人确认终态时点如下：

1. 摄像头打开、拍摄或本地保存失败时，立即将对应槽收敛为
   `PERMANENTLY_MISSING`；
2. 香橙派在照片真正拍到之前重启时，该时点已无法重现，立即收敛为
   `PERMANENTLY_MISSING`；
3. 已拍摄但授权缺失、网络失败或 COS 上传暂时失败时，保持待上传并继续申请授权和退避
   重试；
4. 从真实拍摄时间起超过 72 小时仍未上传成功时，收敛为
   `PERMANENTLY_MISSING`；
5. COS 上传成功、返回 URL 与预期对象路径一致时，收敛为 `AVAILABLE`。

拍摄失败后不重新补拍。因为之后拍摄的内容已经不再是正确的“首次开门前”或“最终关门
后”证据，不能为了凑齐照片而伪造原时点。

#### 4.10.2 有什么作用

后端用该事件将投递订单或清运操作中的一个照片槽归并到最终状态，供管理端展示和人工
审核使用。

它不能：

- 回滚已经成立的投递订单或清运记录；
- 修改重量、金额、袋交换、皮重或满溢结果；
- 自动拒绝返现；
- 反转已经完成的审核或资金结果；
- 阻止该设备后续投递或清运。

投递订单继续进入正常人工审核。照片永久缺失只展示稳定缺失原因，由审核人员结合重量、
其他照片和现场信息判断；不因为缺图自动拒绝返现。清运照片缺失同样只影响证据完整度，
不回滚换袋、新皮重或清运记录。

单次照片缺失也不建立设备故障。只有摄像头持续出现问题达到 4.7 已确认的故障条件时，
才另行产生 `deviceFaultObserved: CAMERA/...`，且当前严重度为 `WARNING`。

#### 4.10.3 作业类型和固定槽位

一个照片槽的业务唯一键为：

```text
(deploymentCode, workType, workUid, slot)
```

投递固定槽位为：

```text
BEFORE_INNER
BEFORE_OUTER
AFTER_INNER
AFTER_OUTER
```

清运固定槽位为：

```text
FIRST_OPEN_INNER
FIRST_OPEN_OUTER
FINAL_CLOSE_INNER
FINAL_CLOSE_OUTER
```

事件必须满足：

- `target.type == payload.workType`；
- `target.uid == payload.workUid`；
- `commandUid=null`；
- `workType=DELIVERY_SESSION` 时只能使用投递槽；
- `workType=CLEAN_OPERATION` 时只能使用清运槽；
- 每个业务唯一键最多产生一条终态事实。

#### 4.10.4 三种终态载荷形状

成功上传时使用：

```text
status        = AVAILABLE
photoUid      = 真实持久化 UUIDv4
url           = 可信 COS HTTPS URL
sha256        = 本地照片真实 SHA-256
sizeBytes     = 真实文件大小
capturedAt    = 真实拍摄时间；时钟不可用时允许 null
missingReason = null
```

URL 必须属于当前可信 COS 环境，并与部署、作业类型、作业身份、槽位和 `photoUid` 推导的
对象路径一致；不得携带查询参数、临时签名或凭证。

对于投递照片，`deliveryComplete` 中已经确认要预先上报的 URL 是“预留目标地址”；
本终态事件中的 URL 则仍表达“对象已实际可用”。因此：

- `AVAILABLE.url` 必须与 `deliveryComplete` 中该槽预留的 URL 完全一致；
- `PERMANENTLY_MISSING.url` 仍为 `null`，不能因为曾经预留过地址而伪报对象存在；
- 后端不得把完成事件里的预留 URL 单独当作上传成功证明，必须同时检查槽位状态。

从未成功拍摄时使用：

```text
status        = PERMANENTLY_MISSING
photoUid      = null
url           = null
sha256        = null
sizeBytes     = null
capturedAt    = null
missingReason = 稳定错误码
```

已经拍摄、但最终无法上传时使用：

```text
status        = PERMANENTLY_MISSING
photoUid      = 真实持久化 UUIDv4
url           = null
sha256        = 已拍文件真实 SHA-256
sizeBytes     = 已拍文件真实大小
capturedAt    = 原真实拍摄时间；时钟不可用时允许 null
missingReason = 稳定错误码
```

缺失原因使用中心化白名单，例如：

- `CAMERA_UNAVAILABLE`
- `PHOTO_CAPTURE_FAILED`
- `EDGE_RESTARTED_BEFORE_CAPTURE`
- `PHOTO_FILE_MISSING`
- `PHOTO_UPLOAD_EXPIRED`

不得把 Python 异常原文、本地路径、COS 响应正文、临时密钥或其他敏感信息放入事件。

#### 4.10.5 本地状态和原子事务

香橙派在尝试拍摄前先为作业和槽位可靠预留身份。摄像头失败本身不能阻止发送投递开门
或清运开始命令，但照片终态和可靠事件必须被保存。SQLite 整体不可写属于独立的
`EDGE_STORAGE` 故障，不等同于普通拍照失败。

每个槽进入终态时，在一个 SQLite 事务中：

1. 校验该槽仍未终结；
2. 保存终态、照片元数据和稳定错误码；
3. 分配或复用唯一持久化 `eventUid` 和 `edgeEventSequence`；
4. 创建不可变事件载荷、摘要和可靠发件箱记录。

事务提交失败时不得留下“照片已终结但事件不存在”的局部状态。同一终态处理重入复用
第一次保存的事件，不生成第二个 `eventUid`。

拍摄成功后上传过程使用原 `photoUid` 和确定性 COS 对象 key。进程在 COS 已接收对象但
本地事务提交前退出时，后续可以幂等覆盖同一对象 key，然后建立唯一终态事实，不能创建
第二个照片身份。

#### 4.10.6 与完成事件的顺序和归并

照片终态可能早于或晚于对应 `deliveryComplete/cleanComplete`：

- 终态事件先到时，后端先按业务唯一键可靠暂存，完成事件到达后归并；
- 完成事件已经包含同一终态和相同元数据时，终态事件作为一致事实幂等接受；
- 完成事件只包含 `UPLOAD_PENDING` 时，终态事件将槽位推进为
  `AVAILABLE/PERMANENTLY_MISSING`；
- 两者终态、`photoUid`、摘要、大小或实际可用 URL 冲突时必须隔离，不能覆盖已有事实；
- 完成事件中的预留 URL 与后续 `AVAILABLE.url` 不一致时必须隔离；后续
  `PERMANENTLY_MISSING.url=null` 不与原预留 URL 构成冲突；
- 状态只能从 `UPLOAD_PENDING` 进入一个终态；
- `AVAILABLE` 和 `PERMANENTLY_MISSING` 均不可互相转换，也不可回退为待上传。

一个槽无论在完成事件之前还是之后终结，都产生自己的唯一
`photoStatusReported`。该规则使照片上传、可靠确认和本地文件清理不依赖完成事件恰好
先到；后端必须容忍两类事件乱序。

后端在终态事件先到且原作业已经存在但完成结果尚未产生时，可以在安全暂存终态后建立
`confirmEdgeEvent`。后续完成事件到达时仍须执行一致性归并。

#### 4.10.7 本地文件和元数据清理

- `AVAILABLE` 事件只有收到 `BUSINESS_APPLIED` 后，才允许删除本地照片二进制；
- `EVENT_QUARANTINED` 时保留本地照片，供诊断和重新处置；
- `PERMANENTLY_MISSING` 一旦可靠建立就停止上传和补授权；
- 达到 72 小时上传期限并建立永久缺失后，可以删除已拍但未上传的本地二进制；
- 照片元数据记录必须至少保留到对应完成事件已经建立并获得业务确认；
- 终态事件先到时，不得仅因该事件获得确认就墓碑化整个照片记录，导致稍后的完成事件
  退回空的 `UPLOAD_PENDING`；
- 作业最终失败且不会产生完成事件时，元数据保留到该作业终态和所有照片事件均已确认，
  再按统一保留策略清理。

#### 4.10.8 后端业务事务和可靠确认

后端必须校验可信 OneNet 来源、Schema、载荷摘要、部署、目标、作业类型、作业身份、
槽位、终态形状和 COS URL。可信处理以业务唯一键和 `eventUid` 双重幂等：

- 同事件同摘要重投复用已有结果和确认；
- 同事件异摘要必须隔离；
- 同一业务唯一键的相同终态事实幂等归并；
- 同一业务唯一键出现第二个不同终态或不同照片内容时隔离；
- 非空 `photoUid` 在全局只能归属一个作业槽。

同一事务完成：

1. 保存可信 edge event；
2. 更新或暂存对应照片槽终态；
3. 保持订单、清运、重量、金额和袋状态不变；
4. 将可信 inbox 标记为已处理；
5. 创建本 `eventUid` 的唯一 `confirmEdgeEvent` 意图。

如果终态合法但原完成事件尚未到达，安全暂存也视为 `BUSINESS_APPLIED`，不需要让香橙派
无限保留已上传二进制。完成事件到达后的内容冲突仍按独立事件隔离处理。

#### 4.10.9 当前实现判断与保留任务

香橙派当前已有以下基础能力：

- 能在拍摄前预留照片身份和槽位；
- 摄像头失败能够建立 `PERMANENTLY_MISSING`；
- 上传失败能够退避、请求新授权并按默认 72 小时保留期重试；
- 上传成功能够校验预期 URL 并建立 `AVAILABLE`；
- 照片终态和可靠事件已经由 `record_photo_status()` 在一个 SQLite 事务中保存；
- `AVAILABLE` 本地文件只有在状态事件收到 `BUSINESS_APPLIED` 后才会删除。

当前仍有以下缺口：

1. 照片终态事件可以早于完成事件产生，而当前状态事件确认后会墓碑化照片记录，可能让
   稍后的完成事件失去真实照片元数据。
2. 当前缺失原因会接受任意符合格式的异常文本或截断后的异常类名，尚无中心化稳定白名单。
3. 完成事件当前没有读取 `PhotoManager` 的真实槽位状态，仍可能固定生成空元数据
   `UPLOAD_PENDING`。
4. 当前照片 URL 只在上传成功时根据临时授权拼出，完成事件没有预先冻结和上报四个正式
   URL；`get_slot_urls()` 还可能回退成本地路径或对象 key，不能满足新确认的稳定 URL
   语义。
5. 当前公共照片 Schema 强制 `UPLOAD_PENDING/PERMANENTLY_MISSING.url=null`，完成事件
   需要改用能够区分“预留目标 URL”和“实际可用 URL”的专用快照形状。
6. 当前代码对拍摄持久化失败的部分前置路径仍可能阻止开门或清运，需要与“普通照片失败
   非阻断、SQLite 故障独立处理”统一。
7. 后端 OneNet 分发仍只处理 `configurationProgress`；本事件会被警告后由 MQ ACK
   丢弃。
8. 后端尚未实现终态先到时的可靠暂存、完成事件到达后的归并和冲突隔离。
9. 后端 `rec_delivery_photo/rec_clean_photo` 对
   `PERMANENTLY_MISSING` 强制清空摘要和大小，不能无损保存“已拍摄但最终无法上传”的
   契约形状。
10. 后端现有 URL 数据库约束与可信 COS 完整路径规则需要统一，并且必须明确区分预留
    URL 和实际可用状态。
11. 后端尚无本事件的业务确认下行和确认回执闭环。
12. 普通可靠事件发布后可能停留在 `SENDING`，尚无完整的重启恢复和确认前重发路径。

后续实施必须覆盖：

- 拍摄失败、拍摄前重启、上传成功和上传满 72 小时四条终态路径；
- 已拍与未拍两种永久缺失载荷；
- 缺图不阻断、不回滚且不自动拒绝返现；
- 每槽唯一终态和稳定 `eventUid`；
- 终态与事件原子提交及 COS 成功后的强杀恢复；
- 终态先到、完成先到和两者一致/冲突归并；
- `AVAILABLE` 业务确认后删除二进制但保留完成事件所需元数据；
- `EVENT_QUARANTINED` 保留本地照片；
- 后端表结构兼容、可信消费、业务确认和重启重传。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.11 `photoUploadGrantRequested`

状态：**已确认**

#### 4.11.1 什么时候发生

当投递或清运的照片已经成功拍摄并可靠保存在香橙派本地、仍处于待上传状态，但当前没有
可用于该作业和槽位的内存 COS 临时授权时，产生 `photoUploadGrantRequested`。

事件只由“存在真实本地照片且需要授权”触发。以下情况不产生本事件：

- 拍摄尚未发生或仍在拍摄队列中；
- 拍照已经失败并收敛为 `PERMANENTLY_MISSING`；
- 照片已经成功上传；
- 当前内存授权仍有效且覆盖该槽位；
- 只是普通网络超时，现有授权仍然有效，可以直接重试。

同一作业存在多个待上传槽位时，合并为一个当前有效申请，不为每张照片各发一个申请。

#### 4.11.2 有什么作用

该事件用于通知后端：

> 香橙派本地有照片等待上传，请通过 `providePhotoUploadGrant` 为原投递或清运作业下发
> 新的 COS 临时授权。

它不是照片上传结果，不表示照片已经存在于 COS，也不改变投递或清运业务状态。事件、
服务和照片终态的含义必须分开：

- `photoUploadGrantRequested`：香橙派需要授权；
- `providePhotoUploadGrant: ACCEPTED`：香橙派已经接收授权并唤醒上传队列；
- `photoStatusReported: AVAILABLE`：某张照片已经实际上传成功；
- `photoStatusReported: PERMANENTLY_MISSING`：某个槽已经最终无法取得照片。

等待授权、授权下发失败或照片上传失败，都不阻塞 `deliveryComplete/cleanComplete`、
建单、清运归并或业务确认。

#### 4.11.3 四种申请原因

`reason` 按以下规则选择：

- `INITIAL_GRANT_MISSING`：第一次准备上传时没有任何可用授权；
- `GRANT_EXPIRED`：授权已经到期，或进入配置的到期安全窗口；
- `EDGE_RESTARTED`：香橙派进程重启，按既定规则已经丢弃仅存在内存中的授权；
- `UPLOAD_RETRY`：COS 明确返回签名、权限、策略或授权范围错误，香橙派已经废弃旧授权，
  需要重新申请后重试。

连接超时、DNS 暂时失败、连接断开和可重试的 COS 服务端错误，不自动废弃仍然有效的
授权，也不立即产生新的申请事件；香橙派继续使用原授权和原对象地址退避重试。只有确认
旧授权不可继续使用时，才换代申请。

#### 4.11.4 槽位、URL 和上传身份

事件必须满足：

- `target.type == payload.workType`；
- `target.uid == payload.workUid`；
- `commandUid=null`；
- `requestedSlots` 只包含已经拍摄、尚未终结且确实缺少可用授权的槽；
- 槽位按对应作业的固定顺序排列，数量为 1 至 4。

`requestedSlots` 是申请创建时的不可变快照。`providePhotoUploadGrant` 按已经确认的服务
规则授权该作业完整四个槽，因此同一作业稍后出现新的待上传照片时，可以使用已经安装的
授权，不要求为了新增槽位修改旧事件。

对于投递照片，项目负责人确认：

> 上传使用的 URL 在 `deliveryComplete` 创建时已经确定。申请新授权和上传重试只获得
> “写入同一对象地址的临时权限”，绝不改变照片的正式 URL。

因此每次上传和重试必须复用同一组稳定身份：

```text
photoUid + slot + objectKey + formalUrl
```

`providePhotoUploadGrant` 中的可信 `baseUrl` 和 `keyPrefix` 必须能推导出该预留
`objectKey/formalUrl`。不匹配时拒绝该授权，不能迁移照片到新地址。临时签名、密钥和
会话令牌不进入正式 URL。

#### 4.11.5 香橙派本地事务和幂等

一个当前申请以以下身份唯一：

```text
(workType, workUid, grantGeneration)
```

创建申请时，在一个 SQLite 事务中：

1. 重新确认照片仍处于可上传的待处理状态；
2. 分配新的持久化 `eventUid` 和 `edgeEventSequence`；
3. 保存不可变申请载荷和摘要；
4. 建立可靠事件发件箱记录；
5. 将对应待上传照片绑定到该申请。

同一代次已有有效申请时，后续轮询、断网和 OneNet 重发必须复用同一个 `eventUid`、
序号和载荷，不得每次生成新申请。服务尚未到达时，同一作业后来拍到的待上传照片可以
绑定到该申请，因为服务下发的是完整四槽授权；已经上报的 `requestedSlots` 不回写、
不扩充。

授权过期、授权被 COS 拒绝或香橙派重启时：

1. 丢弃旧的内存授权；
2. 使旧申请失效；
3. 增加 `grantGeneration`；
4. 使用新的原因和新 `eventUid` 创建申请；
5. 继续使用原 `photoUid/objectKey/formalUrl` 上传。

旧代次的 `providePhotoUploadGrant` 迟到时直接返回 `REJECTED`，不能覆盖新代次授权。

#### 4.11.6 后端处理和业务确认

后端收到事件后校验可信 OneNet 来源、Schema、摘要、部署、原作业、目标、槽位和申请
身份。以 `eventUid` 和申请业务身份双重幂等，在同一个业务事务中：

1. 保存可信申请事件；
2. 创建唯一的 `providePhotoUploadGrant` 下发任务；
3. 标记可信 inbox 已处理；
4. 创建该申请事件的唯一 `confirmEdgeEvent` 意图。

事务成功后使用 `BUSINESS_APPLIED` 确认申请。该结果只表示“后端已可靠保存申请并负责
继续下发授权”，不表示香橙派已经收到授权，更不表示照片上传成功。授权服务可以先于或
晚于该业务确认到达，香橙派都按各自身份独立幂等处理。

同一申请的服务重试保持同一个稳定 `commandUid`。后端可以在每次实际下发前重新签发
新的短期凭证，但不得持久化或记录临时密钥、会话令牌等秘密；新凭证仍必须授权同一作业
的四个固定槽和原确定性对象路径。

重复申请事件复用既有下发任务和业务确认，不重复创建控制任务。新申请代次到达后，旧
下发任务作废；如果处理时所有相关照片都已经进入终态，则将任务记为无需下发并仍可
`BUSINESS_APPLIED`，不再发送无用凭证。

`EVENT_QUARANTINED` 表示该申请本身存在无法安全归并的身份或内容问题。香橙派停止重发
同一个无效申请；只有本地原因已经修正且照片仍在 72 小时保留期内时，才能增加代次并
创建新申请，不能无条件循环制造事件。

#### 4.11.7 重启、期限和失败边界

- 临时授权只存在内存，进程重启后不恢复、不从 SQLite 读取；
- 重启后仍有本地待上传照片时，以 `EDGE_RESTARTED` 创建新代次申请；
- 申请和授权失败不修改完成事件，也不改变投递订单或清运记录；
- 已确认的投递照片始终向 `deliveryComplete` 预留的同一个 URL 重试；
- 从真实拍摄时间起满 72 小时仍未上传成功时，停止申请和上传，按 4.10 收敛为
  `PERMANENTLY_MISSING`；
- `BUSINESS_APPLIED` 只允许停止重发本申请事件，不能删除照片；
- 只有对应 `photoStatusReported: AVAILABLE` 获得业务确认后，才允许删除已上传的
  本地照片二进制。

#### 4.11.8 当前实现判断与保留任务

香橙派当前已经具备以下基础：

- 待上传照片可以按作业归组并创建 `PHOTO_UPLOAD_GRANT_REQUESTED`；
- 申请事件和照片绑定可以在 SQLite 事务中建立；
- 临时授权只存在内存；
- 授权过期和进程重启能够使授权换代；
- 上传使用 `slot/photoUid.jpg` 的确定性对象 key；
- 上传失败会退避重试，照片默认保留 72 小时。

当前仍有以下缺口：

1. 正式 URL 目前到上传时才由授权中的 `baseUrl/keyPrefix` 拼出，没有在
   `deliveryComplete` 前可靠预分配和冻结。
2. 投递完成事件尚未上报四个预留 URL，旧 Schema 也禁止
   `UPLOAD_PENDING/PERMANENTLY_MISSING` 携带预留 URL。
3. 上传异常尚未可靠区分“普通网络错误”和“授权已经不可用”，可能无法准确决定是否
   保留授权或以 `UPLOAD_RETRY` 换代。
4. 申请复用主要依赖照片行中的一个事件 UID，尚无完整、显式的申请代次状态机和迟到
   服务拒绝证明。
5. 已发布为 `SENDING` 的可靠申请事件在重启后仍可能无法恢复到重发状态。
6. 后端尚未消费本事件，也没有凭证签发任务、`providePhotoUploadGrant` 下行、旧代次
   作废、业务确认和回执闭环。
7. 契约、OneNet 物模型、线格式映射、示例、语义校验和测试都需要同步落实预留 URL 与
   实际可用状态分离的语义。

后续实施必须覆盖：初次缺授权、授权过期、进程重启、授权错误、普通网络错误、重复申请、
新旧代次乱序、服务与业务确认乱序、四 URL 稳定性、同对象幂等覆盖、72 小时终结以及
秘密不落盘测试。

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.12 `businessConfirmationReceipt`

状态：**已确认**

#### 4.12.1 什么时候发生

香橙派收到并成功处理后端下发的 `confirmEdgeEvent` 时产生
`businessConfirmationReceipt`。产生回执之前，必须已经在同一个 SQLite 事务中：

1. 完整校验并保存业务确认；
2. 将原 `RELIABLE_FACT` 标记为已确认并停止自动重传；
3. 分配并保存唯一、不可变的确认回执；
4. 提交原事件状态、确认记录和回执发件箱记录。

如果确认命令不合法、与本地原事件不匹配或事务提交失败，则不产生回执，原事件继续
重传。同步服务返回 `ACCEPTED` 只能发生在上述事务提交之后。

#### 4.12.2 有什么作用

该事件用于通知后端：

> 香橙派已经可靠保存本次业务确认，后端可以结束并停止重试对应的
> `confirmEdgeEvent` 控制任务。

它只确认“确认命令已经在边缘可靠落地”，不是第二次业务处理，不会再次修改订单、
清运、配置、满溢、皮重、故障或照片状态。它属于 `CONTROL_RECEIPT`，后端不得再为它
创建另一条 `confirmEdgeEvent`，否则会形成无限确认循环。

本事件与 MCU 无关，不发送 UART、不读取或构造 MCU 状态、不占用物理工作槽，也不影响
后续投递、清运或照片上传。

#### 4.12.3 事件字段

回执必须满足：

- `deliveryClass=CONTROL_RECEIPT`；
- `target.type=BUSINESS_CONFIRMATION`；
- `target.uid == payload.confirmationUid`；
- `commandUid` 等于原 `confirmEdgeEvent` 的 `commandUid`；
- `payload.confirmationUid` 原样复制确认身份；
- `payload.originalEventUid` 原样复制被确认事件身份；
- `payload.originalPayloadSha256` 原样复制被确认事件摘要；
- `payload.outcome` 原样复制 `BUSINESS_APPLIED/EVENT_QUARANTINED`。

回执不重复携带 `effectKind`、`processedAt`、`resultReferences`、`errorCode` 或
`quarantineUid`。这些字段保存在本地完整确认记录和后端冻结的确认任务中；回执通过
`commandUid + confirmationUid + originalEventUid + originalPayloadSha256 + outcome`
与该任务严格绑定。

首次创建时冻结 `eventUid`、`edgeEventSequence`、`occurredAt`、`clockQuality`、完整
载荷和摘要。后续任何技术重发都必须复用第一次保存的完整事件。

#### 4.12.4 幂等和重复确认

同一个原 `RELIABLE_FACT` 只能收敛到一个确认身份和一条回执：

- 相同 `commandUid`、`confirmationUid` 和完整确认内容重复到达时，服务返回
  `DUPLICATE_ACCEPTED`；
- 重复确认必须重新激活第一次创建的同一条回执，复用原 `eventUid`、序号、时间、载荷
  和摘要；
- 相同 `confirmationUid` 但确认内容不同，属于幂等冲突，返回 `REJECTED`；
- 同一原事件已经确认后换用另一个 `confirmationUid` 或 `commandUid`，返回
  `REJECTED`；
- 冲突确认不得覆盖本地既有确认，不得改变原事件终态，也不得生成第二条回执。

`BUSINESS_APPLIED` 和 `EVENT_QUARANTINED` 都按同一套回执规则处理。隔离结果只停止对应
原事件并保留诊断记录，继续遵守 3.8 已确认的“不锁设备、不阻止新业务”规则。

#### 4.12.5 发布、PUBACK 和重启

回执使用 MQTT QoS 1 发送，并持续重试到收到 OneNet/MQTT 的 `PUBACK`。它不等待另一层
业务确认：

1. 发布前保持 `PENDING`；
2. 发布在途时为 `SENDING`；
3. 收到匹配 `PUBACK` 后停止主动重发；
4. 发布失败、连接断开或进程重启时恢复为可重发状态。

`PUBACK` 只表示 OneNet 已接收该次回执发布，不保证后端确认任务已经收到并结束。如果
后端没有收到回执，会继续重发原 `confirmEdgeEvent`；香橙派随后重新激活并发送原回执，
从而闭合该丢失窗口。

为保证迟到重复确认仍能得到同一回执，香橙派收到 `PUBACK` 后不能删除回执身份和不可变
内容。当前阶段保留完整、紧凑的回执记录，不按短时间窗口自动删除。后续如需清理，必须
先设计能够继续逐字段重建同一事件的持久墓碑，不能因为正文已清理而创建新
`eventUid`。

原 `RELIABLE_FACT` 在合法确认事务提交后已经停止重传，不需要等待回执发布成功。回执
暂时发不出去也不能撤销已经保存的业务确认。

#### 4.12.6 后端消费

后端把本事件作为协议控制回执处理，而不是普通领域事件。收到后校验可信 OneNet 来源、
Schema、摘要、部署身份、目标以及以下冻结字段：

```text
commandUid
confirmationUid
originalEventUid
originalPayloadSha256
outcome
```

全部匹配唯一任务 `CONFIRM_EDGE_EVENT:<originalEventUid>` 后：

1. 幂等保存可信回执；
2. 将确认任务标记为 `DONE`；
3. 停止继续下发 `confirmEdgeEvent`；
4. 不产生新的业务确认。

回执可能先于 OneNet 服务调用的 HTTP 成功响应到达，后端仍以可信回执为最终完成依据，
不能要求先看到 HTTP `code=0`。任务已经完成后收到相同回执，按幂等无动作处理。

回执字段与冻结任务不一致时，后端隔离该回执并保留确认任务继续重试；不能用错误回执
结束任务，也不能回滚原事件已经完成的权威业务事务。无法认证、无法解析的报文按可信
收件通用规则处置。

#### 4.12.7 当前实现判断与保留任务

香橙派当前已有以下基础能力：

- 首次确认可以核对原事件及其载荷摘要；
- 确认记录、原事件状态和回执能够在一个 SQLite 事务中保存；
- 可以构造并编码 `BUSINESS_CONFIRMATION_RECEIPT`；
- 回执收到 MQTT `PUBACK` 后可以结束主动发布。

当前仍有以下缺口：

1. `CONFIRM_EDGE_EVENT` 当前绕过普通命令的完整封套、目标、期限和条件字段校验。
2. `confirmation_inbox` 只按 `confirmationUid` 判断重复，不比较冻结命令和完整确认
   内容；异内容可能被误当成普通重复。
3. 本地没有显式保存确认与唯一回执 `eventUid` 的关系，重复确认不会重新激活并发送
   第一次创建的同一回执。
4. 同一原事件换用不同确认身份的冲突规则没有完整实现。
5. 回执及普通事件停留在 `SENDING` 后缺少统一的进程重启恢复。
6. `PUBACK` 后的回执记录保留和迟到重复确认再发送策略尚未明确落实。
7. 后端当前既不会下发 `confirmEdgeEvent`，也不会消费本回执、结束确认任务或处理回执
   冲突。
8. 现有测试只覆盖首次创建和简单重复，尚未覆盖同身份异内容、不同确认身份、回执丢失、
   重复确认唤醒、先回执后 HTTP 响应以及重启恢复。

后续实施至少先用一类可靠事件打通：

```text
业务归并
  → 确认意图和任务
  → confirmEdgeEvent 下发
  → 香橙派原子落库
  → businessConfirmationReceipt 上报
  → 后端确认任务结束
```

本轮只确认处理策略并记录决策，未修改运行代码。

### 4.13 `deviceRuntimeSnapshot`

状态：**已确认**

#### 4.13.1 什么时候发生

`deviceRuntimeSnapshot` 是香橙派主动产生的设备运行快照和心跳。当前阶段按以下时机生成：

1. 香橙派完成启动并成功连接 OneNet 后立即发送一次；
2. MQTT 断线后重新连接成功时立即生成并发送一份最新快照；
3. 正常在线期间默认每 5 分钟生成一次；
4. 多份快照等待发送时只保留最新一份，不补发已经过时的历史快照。

每次实际生成的快照使用新的 `eventUid`、新的持久单调 `edgeEventSequence` 和当时的状态
快照。网络恢复后重新读取当前状态，不能把断网期间积累的旧心跳逐条上报。

#### 4.13.2 有什么作用

后端用本事件更新设备当前运行和诊断投影，包括：

- 香橙派启动代次、软件版本和在线时间；
- fixed-frame UART 链路是否可以工作；
- 香橙派当前活动配置；
- 本地存储和系统时钟状态；
- 尚未业务确认的可靠事件数量；
- 各投口最近的门命令、电磁阀、重量、满溢、烟感和故障摘要。

本事件是 `TELEMETRY_SNAPSHOT`，只表达“生成快照时香橙派的当前投影”。它不能替代：

- `deliveryComplete/cleanComplete` 的业务完成事实；
- `configurationProgress` 的配置应用事实；
- `deviceFaultObserved/deviceFaultRecovered` 的故障生命周期；
- `safetySensorStateChanged` 的真实烟雾状态变化；
- `photoStatusReported` 的照片终态；
- `businessConfirmationReceipt` 的控制确认回执。

快照丢失、合并或乱序只影响当前诊断新鲜度，不能据此补造、撤销或修改上述可靠事实。

#### 4.13.3 事件封套和发送语义

事件必须满足：

- `deliveryClass=TELEMETRY_SNAPSHOT`；
- `target.type=DEVICE_DEPLOYMENT`；
- `target.uid == deploymentCode`；
- `commandUid=null`；
- `edgeEventSequence` 继续使用部署级统一持久序号空间；
- `ports` 使用活动配置中的真实投口数量，并按 `1..N` 唯一、连续、升序上报。

快照使用 MQTT QoS 1，但不进入需要业务确认的 `RELIABLE_FACT` 发件箱，也不接受
`confirmEdgeEvent`。收到 `PUBACK` 后即可释放本次快照；发送失败或进程退出可以丢弃旧
快照，连接恢复后生成新的当前快照。

如果实现为了处理短暂发布失败而保存一个待发快照槽，该槽也只能保存最新一份：
新快照原子覆盖旧快照。不能让周期快照挤占可靠业务事件队列或造成无界存储增长。

#### 4.13.4 fixed-frame 设备级兼容投影

冻结 MCU 无法提供 HELLO、启动身份、固件版本、能力位或状态查询。项目负责人基于
“当前首要目标是跑起来，无法取得的 MCU 状态按正常兼容投影”的原则确认：

```text
edgeBootId          = 香橙派真实、持久的启动代次
edgeVersion         = 香橙派实际软件版本
mcuBootId           = edgeBootId
mcuFirmwareVersion  = fixed-frame-compat
uartProtocolMajor   = null
uartProtocolMinor   = null
capabilityBitmapHex = 0000000000000000
```

`mcuBootId=edgeBootId` 是 fixed-frame 适配层的兼容身份，只允许用于运行快照和本地兼容
关联，不能放进真实 MCU 安全事件或故障事件中冒充 MCU 证据。

协议版本为空、能力位为 0，明确表示没有原生 UART v1 协议和能力声明。后端识别
`fixed-frame-compat + null protocol version` 后，不得因为能力位为 0 阻断业务；香橙派
已经按照本文各服务确认的兼容语义承接实际控制。

`uartState` 按香橙派直接可观测的串口链路填写：

- 串口已经打开且 fixed-frame 读写链路可用时为 `READY`；
- 串口不存在或未连接时为 `DISCONNECTED`；
- 串口持续读写错误或协议字节流无法使用时为 `FAULT`；
- 不使用 `NEGOTIATING/INCOMPATIBLE` 描述 fixed-frame 缺少 UART v1 握手。

`READY` 只证明 fixed-frame 串口链路可以工作，不证明 MCU 支持查询状态、持久去重、
配置投影或 UART v1 的其他能力。

#### 4.13.5 香橙派真实状态字段

以下字段必须使用香橙派真实状态，不能为了显示正常而硬编码：

- `appliedConfig`：香橙派 SQLite 中当前活动配置的版本和双摘要；尚无活动配置时为
  `null`；
- `localStorageState`：根据 SQLite 读写、磁盘空间和完整性检查填
  `HEALTHY/DEGRADED/READ_ONLY/FULL/CORRUPT`；
- `clockState`：根据系统时间同步状态填 `SYNCED/ESTIMATED/UNAVAILABLE`；
- `pendingReliableEventCount`：统计所有尚未获得业务确认的 `RELIABLE_FACT`，包括
  `PENDING/SENDING`，但不包含本运行快照和 `CONTROL_RECEIPT`；
- `edgeVersion`：从实际构建版本读取，不在代码中长期写死。

`appliedConfig` 继续服从 3.1 和 4.2 的兼容语义：它证明配置已在香橙派生效，不证明
冻结 MCU 已接收或应用配置。后端和界面不得把该快照字段显示为“MCU 已同步”。

#### 4.13.6 fixed-frame 投口兼容投影

投口数量和投口身份来自香橙派活动配置，而不是 MCU 查询结果。每个投口按以下规则构造。

门命令字段：

```text
lastDeliveryDoorCommand          = 香橙派最近实际发送的 OPEN/CLOSE；没有时为 NONE
lastDeliveryDoorOutputStatus     = 对应持久命令结果；没有时为 NOT_DISPATCHED
deliveryDoorPhysicalStateBasis   = NOT_OBSERVABLE
```

不能把最后发送过 `CLOSE` 显示成真实物理门位关闭。运行快照不新增门磁事实。

清运锁字段：

```text
cleanLockPowerState              = 活动解锁输出期间 ENERGIZED；其他时候 DEENERGIZED
solenoidHealth                   = OK
cleanDoorStateBasis              = NOT_OBSERVABLE
cleanerPhysicalCloseConfirmed    = 只有真实清运员确认后才为 true
```

`solenoidHealth=OK` 是无法查询 MCU 驱动健康时的运行优先兼容投影。香橙派如果直接观测到
UART 写失败或其他明确故障，必须改为相应非正常状态并建立已经确认的故障事实，不能继续
用 `OK` 覆盖真实错误。清运员关门确认不是 MCU 状态，绝不能伪造。

重量字段使用香橙派持久保存的最近合法 `DD/EF.POST`：

```text
weightMeasurementStatus = STABLE
weightValueAvailable     = true
reportedWeightGrams      = latest DD.POST or EF.POST
weightValueKind          = LAST_OBSERVED
weightSensorHealth       = OK
weightFaultCode          = null
```

如果设备从未收到合法 `DD/EF`，`reportedWeightGrams` 兼容为 0 克，其余仍按正常值构造，
以避免冻结 MCU 无查询能力导致设备永久不可运行。该 0 克只是 fixed-frame 初始兼容
投影，不是一次真实空袋测量，不得由后端据此创建皮重、订单或满溢事实。

每个最近重量或初始 0 克兼容投影使用持久化的兼容 `weightMeasurementUid`，相同观测在
周期快照中复用该身份；`weightMcuBootId/weightMcuEventSequence` 为空，避免冒充真实
MCU 事件身份。`measurementElapsedMs=0`、`calibrationVersion=0`；有合法历史时
`weightSampleCount=1`，无历史兼容值时为 0。

满溢字段沿用 3.6 和 4.5 已确认的 fixed-frame 观测：

```text
fullnessSensorKind       = DIGITAL_INFRARED
fullnessSensorValue      = latest FULL == 1 ? BLOCKED : CLEAR
fullnessSampleBasis      = NOT_SAMPLED
representativeDistanceMm = null
fullnessValidSampleCount = 有合法 DD/EF 时 1，否则 0
```

从未收到合法 `DD/EF` 时使用 `CLEAR`。该字段只是最新红外观测，不替代重量公式产生的
正式满溢检测结果。

烟感字段持续使用 4.9 已确认的运行优先兼容投影：

```text
smokeState        = NORMAL
smokeSensorHealth = OK
```

这不会产生虚假的 `safetySensorStateChanged`，也不表示 MCU 实际报告了一次正常采样。

`faultBitmap` 默认 0。香橙派已经直接观测并持久化的 UART、存储、摄像头、网络、时钟等
活动故障必须进入相应范围的故障摘要；不得用默认 0 覆盖真实活动故障。无法观测的 MCU
内部故障不自行推断。

#### 4.13.7 后端归并和优先级

后端收到快照后校验可信 OneNet 来源、Schema、摘要、部署目标、字段组合和连续投口列表，
然后更新部署及投口运行投影。

归并规则为：

- 相同部署只应用比当前投影更新的 `edgeEventSequence`；
- 迟到旧快照可以保留接收诊断，但不能覆盖新投影；
- 新 `edgeBootId` 表示香橙派新进程代次，不能反向应用旧启动代次的快照；
- 收到快照可以更新设备最后在线时间；
- 连续超过运维配置的心跳期限未收到新快照时，可以将设备标记离线或状态过期；
- 单次快照丢失不产生可靠故障事件。

可靠事实的优先级高于运行快照：

- 正常快照不能关闭活动故障、烟雾报警或安全阻断；
- 快照中的配置不能代替 `configurationProgress: APPLIED`；
- 快照中的重量和满溢不能创建订单、清运记录、皮重或正式满溢样本；
- 快照中的门和锁投影不能完成或恢复投递、清运；
- `pendingReliableEventCount` 只能用于积压告警和诊断，不能补齐缺失事件。

后端不为本事件创建 `confirmEdgeEvent`。可信收件完成后即可 ACK OneNet 北向 MQ 消息，
最新状态投影更新失败时按普通收件任务重试，但不能要求香橙派永久重传某一份旧快照。

#### 4.13.8 当前实现判断与保留任务

香橙派当前已有以下基础：

- 启动完成后可以发送一份运行快照；
- 主循环默认每 5 分钟发送一次；
- 事件封套使用 `TELEMETRY_SNAPSHOT`、部署目标和空 `commandUid`；
- fixed-frame 路径已经将 UART 协议版本设为空、能力位设为 0；
- 快照直接发送，不进入可靠业务事件确认链。

当前仍有以下缺口：

1. MQTT 重连后没有明确立即生成最新快照的闭环。
2. fixed-frame 投口当前大量使用
   `UNKNOWN/SENSOR_FAULT/weightValueAvailable=false`，与本节和 4.9 已确认的正常兼容投影
   冲突。
3. 兼容模式没有从活动配置读取完整投口列表，可能固定只上报一个投口。
4. `edgeVersion`、`localStorageState` 和 `clockState` 当前分别硬编码为版本字符串、
   `HEALTHY` 和 `SYNCED`，没有读取真实运行状态。
5. `pendingReliableEventCount` 当前只计算最多 1000 条可立即发送的 `PENDING` 事件，
   没有统计所有未确认 `RELIABLE_FACT`，并可能混入其他交付类别。
6. 当前取得了活动故障列表却没有把真实故障映射到投口 `faultBitmap`。
7. 最近 fixed-frame `DD/EF` 的重量和红外观测尚未完整、持久地进入周期运行快照。
8. 当前每次快照直接发布，没有显式的最新快照合并槽和 MQTT 重连触发。
9. 后端 OneNet 分发仍只处理 `configurationProgress`；本事件会被警告后由 MQ ACK
   丢弃，不会更新运行投影和最后在线时间。
10. 后端尚未实现快照新旧判断、fixed-frame 兼容解释以及“可靠事实高于正常快照”的
    合并保护。

后续实施必须覆盖：启动、重连、5 分钟周期、快照合并、断网丢弃旧快照、真实投口数量、
有/无 `DD/EF` 历史、正常兼容投影、真实故障覆盖、可靠事实优先、乱序快照和无递归确认
测试。

本轮只确认处理策略并记录决策，未修改运行代码。

## 5. 后续记录规则

后续每完成一个服务或事件的讨论，都在本文对应条目中记录：

1. 事件在什么时候发生；
2. 事件有什么作用；
3. 当前硬件事实；
4. 项目负责人确认的运行语义；
5. 香橙派处理步骤；
6. OneNet 事件上报策略；
7. 幂等、重启和失败边界；
8. 当前实现是否满足；
9. 明确保留的实现任务。

只有项目负责人明确确认后，才把逐项进度从“待讨论”改为“已确认”。
