# EcoBin P0 目标接口设计：投递、审核与钱包（I-021～I-025）

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-021～I-025 已确认；2026-07-24 已按“一次有效扫码会话一单”重写**
>
> 说明：本文件定义普通用户扫码选择投口、建立投递会话、查询会话和订单、后台审核/纠错以及钱包三项视图的目标 HTTP 契约。设备命令、OneNet 事件、COS 补传和 UART 字段已由 [`I-041～I-045`](09-onenet-cos-edge-confirmation-i041-i045.md) 与 [`I-046～I-050`](10-uart-protocol-i046-i050.md) 承接；这些后续协议不得重新引入会话内云端分段作业或中间过程上报。

## 本章统一边界

1. 普通小程序接口只接受固定机构的 `aud=miniapp` 会话。部署二维码和 URL 中的 `deploymentCode` 只是资源定位信息，后端仍须把当前 AppID、机构用户、部署机构和租户放在可信上下文中重新核对。
2. 扫码查询不取得设备执行权。只有 I-022 的启动命令成功提交后，才形成投递会话、整机 `DELIVERY` 占位、整场冻结快照和唯一设备开始命令；它仍不表示门已经打开。
3. **一次有效扫码建立的 `sessionUid` 对应整场投递，最终最多创建一笔订单。目标模型不再生成、保存、传输或查询会话内第二作业身份。**
4. `START_DELIVERY_SESSION` 以 `sessionUid` 授权整场。会话内每次关门后的“继续投递”只由香橙派与 MCU 在本地重新打开同一投递门，不上云、不重新授权、不创建第二命令或订单，也不重新读取价格、袋或配置。
5. 中间开关门、按钮、轮次重量、照片、普通满溢判断和过程事件都不上传。用户选择结束或本地 30 秒选择窗口届满后，设备才可靠保存并上报整场唯一 `DELIVERY_COMPLETE`。
6. 整场使用开始事务冻结的机构用户、部署、投口、当前袋、单价、设备配置、投递规则和阈值。最终结算重量始终为“最终关门后重量－首次开门前重量”；中间重量不参与逐段结算。
7. 普通满溢检测只在 `DELIVERY_COMPLETE` 建单事务建立 gate 后执行，其结果只影响下一次会话。当前用户继续投递时不按中间重量或红外满溢判断阻断；门卡滞、执行器故障、称重过载、烟雾报警、本地存储不可写等硬安全故障仍必须阻止再次开门。
8. 会话冻结 `negativeWeightThresholdGram`，默认 `500` 克。设备发现任一轮次重量减少量大于等于该阈值时，将 `negativeWeightAnomaly=true` 锁存到整场结束。该布尔值只作为最终订单数据的一部分上报；不单独上报事件，不上传中间减少值，也不改变整场净重量。
9. P0 投递订单全部人工审核。订单审核只有“按原数据通过”和“修改最终重量后通过”，没有驳回；审核/纠错人员不能输入最终金额。
10. 订单完成、照片可用、满溢检测结束、审核通过和钱包变动是不同事实。四张整场照片缺失不阻断建单、审核或返现；负重量异常标志不等于已经认定用户取走物品。

### 路径与能力基准

机构 Web 与平台协助路径分别使用：

```text
普通租户：/api/v1/web/organizations/{organizationCode}
平台协助：/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}
```

本章新增的 Web 能力如下：

| 能力码 | 允许作用域 | 含义 |
|---|---|---|
| `delivery.read` | `TENANT`、`ORGANIZATION` | 查询作用域内投递订单、原始证据、异常、照片状态和认定历史 |
| `review.execute` | `TENANT`、`ORGANIZATION` | 查询相应审核队列及完成投递初审和提现审核；清运记录不审核，本章先落地投递查询/审核端点 |
| `delivery.correct` | `TENANT`、`ORGANIZATION` | 对已经通过的投递订单执行不限期纠错 |
| `wallet.read` | `TENANT`、`ORGANIZATION` | 查询机构用户钱包三项视图及真实资金明细 |

租户主体账号、租户总部人员和机构负责人继续按 I-014 的天然能力规则取得其作用域内相应能力；其他工作人员由授权集合取得。`user.read`、`delivery.read` 和 `wallet.read` 互不隐含。活跃平台管理员通过平台受信上下文使用平台镜像端点，不把上述租户能力伪装成跨租户授权。

## I-021 扫码设备与投递选项查询

**已确认：扫码后先执行无副作用的投递选项查询；查询可以展示当前阻断原因，但不创建会话、不占用设备，也不能作为稍后开门的授权凭证。**

### 1. 查询端点

```http
GET /api/v1/miniapp/device-deployments/{deploymentCode}/delivery-options
Authorization: Bearer <aud=miniapp token>
```

- 未绑定手机号的机构用户仍可查询，以便页面提示先完成手机号绑定。
- `deploymentCode` 不属于当前会话机构、部署不存在或二维码已失效时统一返回 `404 RESOURCE.NOT_FOUND`，不得暴露其他机构、硬件 SN 或当前占用者。
- 查询不要求 `Idempotency-Key`，也不下发设备命令。

`data` 示例：

```json
{
  "deploymentCode": "dpl_4s8V...",
  "displayName": "A区1号回收箱",
  "address": "A区北门",
  "deviceBusy": false,
  "asOf": "2026-07-24T08:30:15.123Z",
  "ports": [
    {
      "portNo": 1,
      "displayName": "一号投口",
      "unitPriceYuanPerKg": "0.8000",
      "fullnessPercent": "35.20",
      "deliveryAllowed": true,
      "blockers": []
    }
  ]
}
```

字段边界：

- 设备和投口展示字段来自当前最高的精确 `APPLIED` 配置；没有可用于新作业的配置时，名称、地址或价格按字段规则返回 `null`，不能回退到未应用草稿或设备自报值。
- `fullnessPercent` 允许大于 `"100.00"`；重量、有效基准或阈值不可用时为 `null`，不能用 `"0.00"` 冒充未知。
- `deviceBusy` 只表达当前存在整机投递/清运占位，不返回占用主体、会话号或清运员信息。
- `deliveryAllowed` 和 `blockers[]` 是查询时快照；I-022 必须在写事务中从锁根重新校验。

阻断枚举至少覆盖：

```text
PHONE_BINDING_REQUIRED
WALLET_DELIVERY_LIMIT_REACHED
TENANT_DISABLED
ORGANIZATION_DISABLED
DEPLOYMENT_NOT_ENABLED
BUSINESS_SWITCH_DISABLED
CONFIGURATION_NOT_APPLIED
EDGE_OFFLINE
MCU_OFFLINE
PROTOCOL_INCOMPATIBLE
SAFETY_LOCKED
DOOR_NOT_CLOSED
PORT_DISABLED
PORT_SENSOR_UNHEALTHY
PORT_FULL
CURRENT_BAG_MISSING
WEIGHT_BASELINE_MISSING
BASELINE_REMEASUREMENT_ACTIVE
FULLNESS_CHECK_PENDING
DELIVERY_RESULT_PENDING
PORT_CLEAN_OPERATION_ACTIVE
DEVICE_BUSY
```

钱包余额达到或低于机构负余额停投阈值时出现 `WALLET_DELIVERY_LIMIT_REACHED`。已锁存的 `MANUAL_RECOVERY_REQUIRED` 只能由已冻结的真实非零人工调整恢复；查询不能自行清除。当前袋始终必需；重量模式使用重量参与满溢判断时还要求当前袋的有效基准。称重硬件健康对所有模式均是投递结算前提。

`DELIVERY_RESULT_PENDING` 表示此前 `sessionUid` 已经可能改变箱内内容，但唯一完成结果尚未形成订单和投递后满溢检测 gate。该阻断只作用于原投口；它必须由迟到的原 `DELIVERY_COMPLETE`、真实清运换袋或 I-030 的受约束恢复接管，不能靠普通重新检测绕过。

## I-022 建立并授权整场投递会话

**已确认：recycling 只在开始投递时开启一次复合授权事务，原子建立会话、整机占位、整场冻结快照和唯一开始命令；不存在继续投递后端用例。**

### 1. 启动端点

```http
POST /api/v1/miniapp/device-deployments/{deploymentCode}/ports/{portNo}/delivery-sessions
Authorization: Bearer <aud=miniapp token>
Idempotency-Key: <UUIDv4>
Content-Type: application/json

{}
```

请求不接受用户、租户、机构、价格、配置版本、袋码、余额、设备状态或任何轮次身份等客户端自报事实。当前用户和作用域来自会话，其他事实只能由服务端权威数据确定。

### 2. 唯一开始复合事务

服务端严格使用 D-037 锁序：`tenant → organization → miniapp → delivery config head → organization user → wallet → device asset`，再沿部署、运行态、占位、投口、当前袋和容量前进。事务内重新检查：

- 用户有效且已绑定手机号；
- 钱包余额严格高于当前负余额停投阈值，且没有锁存的 `MANUAL_RECOVERY_REQUIRED`；
- 租户、机构、部署、经营开关和投口有效；
- 当前最高配置已经精确 `APPLIED`，香橙派、MCU、投递门、称重和协议健康；
- 当前投口未满，没有未终结满溢检测、基准重测、清运操作或 `DELIVERY_RESULT_PENDING`；
- 当前袋、有效价格和按满溢模式要求的基准存在；
- 整机没有投递/清运占位，当前用户没有另一条活动投递会话。

全部通过后，在同一事务完成：

1. 生成 UUIDv4 `sessionUid`，创建 `ACTIVE/START_QUEUED` 会话；
2. 冻结用户、部署、投口、当前袋、配置版本及摘要、投递规则版本、单价、负余额阈值、`continueDeliveryWaitMs`、`negativeWeightThresholdGram`、人工认定重量上限和照片槽定义；
3. 取得指向该会话的整机 `DELIVERY` 占位；
4. 创建唯一 `START_DELIVERY_SESSION` 设备命令和唯一可靠任务；
5. 以事务提交时间计算默认 60 秒的 `startAuthorizationExpiresAt`，把期限和冻结摘要写入命令；
6. 写成功审计。

任一步失败整体回滚。此时不创建订单、满溢检测或第二阶段授权。

香橙派收到命令后必须先把完整会话、冻结摘要和本地单调截止点可靠写入 SQLite，再取得首次开门前两张照片和稳定总重量。只有命令仍在开始期限内、摘要一致且硬安全条件成立时，才允许 MCU 首次打开投递门。开始期限只限制首次执行；一旦在期限内开始，整场继续投递沿用同一授权，不再访问后端资格、价格、袋或满溢状态。

### 3. 受理响应、幂等与冲突

成功返回 `202 Accepted`，`Location` 指向 I-023 的会话资源：

```json
{
  "code": "OK",
  "data": {
    "operationId": "8e8ebf0e-...",
    "resourceId": "8d476b7d-...",
    "sessionUid": "8d476b7d-...",
    "status": "ACTIVE",
    "phase": "START_QUEUED",
    "startAuthorizationExpiresAt": "2026-07-24T08:31:15.123Z",
    "statusUrl": "/api/v1/miniapp/delivery-sessions/8d476b7d-...",
    "recommendedPollAfterMs": 1000,
    "nextActions": ["WAIT"]
  },
  "requestId": "01..."
}
```

- `202` 只证明会话、快照、占位和可靠命令共同提交，不证明 OneNet 已转发、设备已受理或门已打开。
- 同一 `Idempotency-Key` 同摘要重试返回原结果，不重复占位或下发。当前用户使用新键重复启动返回 `409 DELIVERY.SESSION_ALREADY_ACTIVE`；其他主体占用设备时返回 `409 DEVICE.DEVICE_BUSY`，不泄露占用者。
- 受理前失败不永久占用幂等成功槽；客户端复用原键重试同一意图时，服务端重新检查当前资格。

主要错误包括 `IDENTITY.PHONE_BINDING_REQUIRED`、`WALLET.DELIVERY_LIMIT_REACHED`、`DEVICE.DEPLOYMENT_UNAVAILABLE`、`DEVICE.CONFIGURATION_NOT_APPLIED`、`DEVICE.PORT_UNAVAILABLE`、`DEVICE.PORT_FULL`、`DEVICE.FULLNESS_CHECK_PENDING`、`DEVICE.CURRENT_BAG_MISSING`、`DEVICE.WEIGHT_BASELINE_MISSING`、`DEVICE.BASELINE_REMEASUREMENT_ACTIVE`、`CLEAN.PORT_OPERATION_ACTIVE`、`DEVICE.DEVICE_BUSY` 和 `DELIVERY.SESSION_ALREADY_ACTIVE`。

## I-023 投递会话、唯一完成结果与收敛查询

**已确认：小程序只观察整场会话；继续/结束由设备本地处理。后端只接收最终一次 `DELIVERY_COMPLETE`，据此创建一单并在结束后建立满溢检测。**

### 1. 会话查询

```http
GET /api/v1/miniapp/delivery-sessions/{sessionUid}
Authorization: Bearer <aud=miniapp token>
```

不存在会话周期列表接口。只有原机构用户能读取自己的会话；其他用户、其他机构或不存在统一返回 `404 RESOURCE.NOT_FOUND`。查询不推动状态，也不能发出继续、结束或远程开门。

`data` 示例：

```json
{
  "sessionUid": "8d476b7d-...",
  "status": "ACTIVE",
  "phase": "IN_PROGRESS",
  "deploymentCode": "dpl_4s8V...",
  "portNo": 1,
  "startedAt": "2026-07-24T08:30:20.123Z",
  "endedAt": null,
  "endReason": null,
  "deliveryOrderNo": null,
  "recommendedPollAfterMs": 1000,
  "nextActions": ["WAIT_ON_DEVICE"]
}
```

`phase` 固定为：

```text
START_QUEUED
IN_PROGRESS
FINAL_RESULT_PENDING
BUSINESS_CONFIRMED
PRE_START_FAILED
RECOVERY_REQUIRED
```

设备不会向后端逐轮报告当前开关门阶段、继续窗口、轮次序号或中间重量，因此 HTTP 查询也不得推测或返回这些字段。`nextActions` 只使用 `WAIT`、`WAIT_ON_DEVICE`、`VIEW_ORDER`、`SESSION_ENDED`；它是展示建议，不是设备授权。

### 2. 本地继续、结束和负重量锁存

1. 每次投递门真实关闭后，MCU/香橙派在本地取得本轮关门后稳定重量，并按本会话冻结的 `continueDeliveryWaitMs` 开启选择窗口。
2. 用户在 30 秒窗口内选择继续时，设备只检查真实投递门可再次动作且不存在硬安全故障，然后在原 `sessionUid` 下重新开门。普通红外/重量满溢值、中间净增量、后端订单状态、网络和后来配置均不参与本次继续判断。
3. 为计算异常标志，设备可在 SQLite 中保存恢复所需的本轮前后重量；这些重量、按钮、开关门事实和任何中间照片不得进入 OneNet 事件或后端订单数据。任一轮次减少量达到冻结阈值后，`negativeWeightAnomaly` 只从 `false` 锁存为 `true`。
4. 用户选择结束或选择窗口届满时，不再重新开门。设备取得最终关门后稳定重量及两张最终照片，把整场结果和唯一可靠事件原子写入 SQLite 后再上报。
5. 其他用户在整机占位存在期间扫码只能看到设备忙，不能结束、覆盖或继承原会话。

### 3. 唯一 `DELIVERY_COMPLETE`

最终事件属于订单数据的一部分，至少包含：

```json
{
  "eventUid": "2c496616-...",
  "sessionUid": "8d476b7d-...",
  "commandUid": "03fceaa1-...",
  "edgeEventSequence": 1042,
  "endTrigger": "USER_ENDED",
  "firstPreOpenWeightGram": 102300,
  "finalPostCloseWeightGram": 104800,
  "firstPreOpenWeightStatus": "RELIABLE",
  "firstPreOpenWeightFaultCode": null,
  "finalPostCloseWeightStatus": "RELIABLE",
  "finalPostCloseWeightFaultCode": null,
  "finalDeliveryDoorState": "CLOSED",
  "negativeWeightAnomaly": true,
  "snapshotSummary": {
    "configurationVersion": 4,
    "configurationSha256": "1b4a...",
    "deliveryRuleVersion": 3,
    "bagQr": "BAG_A8x...",
    "unitPriceYuanPerKg": "0.8000",
    "negativeWeightThresholdGram": 500
  },
  "photos": [
    {"slot": "BEFORE_INNER", "status": "AVAILABLE", "url": "https://..."},
    {"slot": "BEFORE_OUTER", "status": "AVAILABLE", "url": "https://..."},
    {"slot": "AFTER_INNER", "status": "AVAILABLE", "url": "https://..."},
    {"slot": "AFTER_OUTER", "status": "AVAILABLE", "url": "https://..."}
  ],
  "deviceOccurredAt": "2026-07-24T08:35:30.123Z"
}
```

- `endTrigger` 只允许 `USER_ENDED/CONTINUE_WINDOW_EXPIRED`。重量字段是有符号整数克；不可靠时值为 `null`，对应 `WeightStatus` 必须明确非可靠状态且 `WeightFaultCode` 非空，不能用 0 或最近一次中间重量兜底。可靠时状态为 `RELIABLE` 且故障码为空。
- `negativeWeightAnomaly` 必填且只表示整场本地曾有至少一轮达到减少阈值。事件不包含触发轮次、轮次前后重量、减少克数、按钮次数或中间照片；该标志不是独立上报。
- `snapshotSummary` 必须与后端会话冻结快照一致。设备回传它是为了验证原授权，不允许借此自报新单价、新袋或新配置。
- 四个照片槽固定为整场首次开门前内外和最终结束关门后内外。状态可以是 `AVAILABLE/UPLOAD_PENDING/PERMANENTLY_MISSING`；缺图不阻断建单。

后端处理同一事务必须：

1. 验证可信来源、原 `sessionUid/commandUid`、冻结摘要、最终投递门关闭和事件幂等身份；
2. 保存唯一整场物理结果，并以 `finalPostCloseWeightGram - firstPreOpenWeightGram` 计算原始净重量；
3. 为该 `sessionUid` 创建且仅创建一笔投递订单和四个照片槽，把 `negativeWeightAnomaly` 记录为订单异常标志；不保存任何中间减少值；
4. 使用开始时冻结的单价、袋和配置创建投递后满溢检测 gate/必要采样任务。检测结论不回写本单，只决定下一次会话；
5. 把会话推进为 `BUSINESS_CONFIRMED`、关联订单、释放匹配整机占位，并与 inbox 完成和业务确认意图共同提交。

`sessionUid` 单独作为最终物理结果和订单的业务唯一根；`eventUid` 只负责可靠事件去重和冲突检测，不能只建立 `(sessionUid,eventUid)` 复合唯一键。相同 `eventUid + sessionUid + 摘要` 重投返回原订单；同一 `eventUid` 异摘要、同一 `eventUid` 改挂 session，或同一 session 使用第二个 `eventUid` 再报完成，都进入隔离并保留首个可信结果，绝不能覆盖或创建第二单。订单审核和钱包仍按 I-024/I-025 后续发生。

### 4. 失败、迟到结果和安全恢复

- 开始授权到期或设备明确证明从未执行首次开门时，会话可推进为 `PRE_START_FAILED` 并释放占位，不创建订单或满溢检测。命令是否可能已执行或投递门状态不明确时，必须保留占位/安全锁，不能以超时猜测安全。
- 已经可能发生投递但最终事件暂未到达时，不创建零重量或无主替代单。只有在能够证明投递门已安全关闭且旧命令不会再动作时，受控恢复才可在释放整机占位前把原 `sessionUid` 锁存为该投口 `DELIVERY_RESULT_PENDING`；原投口继续阻断，其他安全投口可按各自条件工作。
- 后到的原 `DELIVERY_COMPLETE` 仍只按原 `sessionUid` 和冻结摘要建原订单，并在订单及结束后检测 gate 共同成立后清除匹配阻断。它不重新激活会话、抢回设备或归给后来用户。
- 无可信完成结果时，只能由 I-030 的现场核查恢复或真实清运换袋接管，不能通过普通满溢重检、继续按钮或后台改状态跳过。

## I-024 投递订单查询、初审与不限期纠错

**已确认：普通用户可查看自己的原始订单和当前认定；后台用统一审核能力完成首次认定，用独立纠错能力追加后续版本，并在同一事务按系统计算金额改变钱包。**

### 1. 普通用户和 Web 查询端点

普通用户：

```http
GET /api/v1/miniapp/me/delivery-orders?cursor={opaque}&limit={1..100}
GET /api/v1/miniapp/me/delivery-orders/{deliveryOrderNo}
```

普通 Web：

```http
GET /api/v1/web/organizations/{organizationCode}/delivery-orders
GET /api/v1/web/organizations/{organizationCode}/delivery-orders/{deliveryOrderNo}
```

平台镜像：

```http
GET /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/delivery-orders
GET /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/delivery-orders/{deliveryOrderNo}
```

- 时间线展示顺序按 `deviceOccurredAt + deliveryOrderNo` 稳定倒序，但首屏还会冻结当前机构订单提交可见序号作为 `snapshotHighWatermark`。该水位只冻结“哪些订单已经创建”的候选全集：后续页只读取不超过该上限的订单，翻页期间新建的旧发生时…42 tokens truncated…rredAt + deliveryOrderNo`；响应返回可空 `nextCursor`。订单列表的 `limit` 默认 20、最高 100；改变筛选必须从首屏重新查询。游标签名、版本或筛选不匹配，以及超过服务端保留期时返回 `400 COMMON.INVALID_CURSOR`，不得降级猜测分页位置或使用无索引大偏移。
- 小程序列表固定当前用户和机构，只接受 `cursor`、`limit` 及可选 `reviewStatus`。Web 列表允许 `reviewStatus`、`occurredFrom`、`occurredTo`、`organizationUserUid`、`deploymentCode`、`portNo`、`anomalyCode`、`photoCompleteness=COMPLETE/INCOMPLETE`、`cursor` 和 `limit`；`occurredFrom` 包含边界，`occurredTo` 不包含边界，时间格式遵守 I-002。所有筛选都与实时授权机构求交。
- `reviewStatus` 和 `photoCompleteness` 是可变投影，必须在每一页读取时按当前值重新判断，不属于跨请求的历史 as-of 快照。因此并发审核或照片补传可以让尚未翻到的既有订单进入或退出后续页；稳定排序键保证已经返回的订单不会在同一游标链中重复，客户端需要完整当前工作队列时必须刷新首屏。响应 `asOf` 是本页服务端观察时间，不声称整条游标链共享同一状态时点。其余基于订单不可变创建事实的筛选继续受创建水位稳定约束。
- Web 列表允许 `delivery.read` 或 `review.execute`：前者用于普通订单查询，后者必须能形成待审核工作队列。订单详情允许 `delivery.read`、`review.execute`，或仅为完成纠错而对直接目标持有 `delivery.correct`；`delivery.correct` 单独不开放订单列表。平台镜像要求活跃平台管理员。
- 只有 `review.execute` 而没有 `delivery.read` 时，列表固定为当前可审核的 `PENDING` 订单，详情也只开放仍可审核的直接目标；不能借审核能力浏览全部已通过历史。`delivery.correct` 的直接目标读取同样只返回执行纠错所需的安全详情和当前修订版本。
- 订单不属于当前可见作用域时统一返回 `404 RESOURCE.NOT_FOUND`。

### 2. 订单列表 DTO

列表响应 `data` 固定为：

```json
{
  "items": [
    {
      "deliveryOrderNo": "DO20260723...",
      "organizationUserUid": "54f9c3f5-...",
      "deploymentCode": "dpl_4s8V...",
      "portNo": 1,
      "deviceOccurredAt": "2026-07-23T08:31:06.123Z",
      "receivedAt": "2026-07-23T08:31:08.123Z",
      "rawWeightKg": "-1.25",
      "rawAmountYuan": "-1.00",
      "rawWeightReliability": "RELIABLE",
      "rawAmountReliability": "RELIABLE",
      "reviewStatus": "PENDING",
      "currentRevisionNo": 0,
      "finalWeightKg": null,
      "finalAmountYuan": null,
      "anomalyCodes": ["NEGATIVE_WEIGHT_ANOMALY"],
      "photoCompleteness": "INCOMPLETE"
    }
  ],
  "asOf": "2026-07-23T08:45:15.123Z",
  "nextCursor": null
}
```

字段规则：

- `organizationUserUid` 在 Web 中必填且来自原 session；普通小程序 DTO 不含该字段。无法验证原 session 和用户归属的完成结果只进入技术隔离，不出现在订单列表。
- `rawWeightKg/rawAmountYuan` 在对应原始计算不可靠时为 `null`。`rawWeightReliability` 固定为 `RELIABLE/MISSING/INVALID/INCONSISTENT`；`rawAmountReliability` 固定为 `RELIABLE/WEIGHT_UNRELIABLE/PRICE_UNAVAILABLE`，重量不可靠优先归为 `WEIGHT_UNRELIABLE`。
- `reviewStatus` 固定为 `PENDING/APPROVED`。待审核时 `currentRevisionNo=0` 且最终值均为 `null`；通过后修订号至少为 1 且最终值非空。
- `anomalyCodes` 只返回稳定安全代码，不返回诊断 JSON。`photoCompleteness` 固定为 `COMPLETE/INCOMPLETE`。

### 3. 订单详情 DTO

详情 `data` 使用以下稳定结构；字段均出现，只有标明 Web-only 的字段在普通小程序响应中整体省略：

```json
{
  "deliveryOrderNo": "DO20260723...",
  "source": {
    "eventUid": "2c496616-...",
    "sessionUid": "8d476b7d-...",
    "deploymentCode": "dpl_4s8V...",
    "portNo": 1,
    "deviceOccurredAt": "2026-07-23T08:31:06.123Z",
    "receivedAt": "2026-07-23T08:31:08.123Z"
  },
  "ownership": {
    "organizationUserUid": "54f9c3f5-..."
  },
  "raw": {
    "firstPreOpenWeightGram": 102300,
    "finalPostCloseWeightGram": 101050,
    "netWeightGram": -1250,
    "weightKg": "-1.25",
    "unitPriceYuanPerKg": "0.8000",
    "amountYuan": "-1.00",
    "weightReliability": "RELIABLE",
    "amountReliability": "RELIABLE",
    "negativeWeightAnomaly": true
  },
  "review": {
    "status": "APPROVED",
    "currentRevisionNo": 1,
    "maxReviewAbsoluteWeightKg": "100.00",
    "finalWeightKg": "-1.25",
    "finalAmountYuan": "-1.00",
    "firstApprovedAt": "2026-07-23T08:40:15.123Z"
  },
  "anomalies": [
    {
      "category": "SYSTEM",
      "code": "NEGATIVE_WEIGHT_ANOMALY",
      "detectedAt": "2026-07-23T08:31:08.123Z",
      "message": "投递过程中检测到达到阈值的重量减少，等待人工确认",
      "diagnosticDetails": null
    }
  ],
  "photos": [
    {
      "position": "BEFORE_INNER",
      "status": "PERMANENTLY_MISSING",
      "url": null,
      "capturedAt": null,
      "missingReason": "DEVICE_DID_NOT_PRODUCE_PHOTO"
    }
  ],
  "revisions": [
    {
      "revisionUid": "593eea37-...",
      "revisionNo": 1,
      "revisionType": "INITIAL_REVIEW",
      "decision": "ORIGINAL_APPROVED",
      "beforeFinalWeightKg": null,
      "beforeFinalAmountYuan": null,
      "afterFinalWeightKg": "-1.25",
      "afterFinalAmountYuan": "-1.00",
      "amountDeltaYuan": "-1.00",
      "reason": null,
      "operator": {
        "actorKind": "STAFF_ACCOUNT",
        "actorUid": "246ead7a-...",
        "displayName": "审核员甲"
      },
      "reviewedAt": "2026-07-23T08:40:15.123Z"
    }
  ]
}
```

空值、枚举和可见性：

- 首次开门前和最终关门后克值分别可空；缺失时不能用 0 或中间重量代替。`raw.weightKg/amountYuan` 及两种可靠性与列表口径一致。
- `source.eventUid` 和 `source.sessionUid` 始终非空，且一个会话只能关联一单。无法恢复原会话、用户或冻结摘要的数据进入技术隔离，不能伪造会话或借用后来用户身份。
- `raw.negativeWeightAnomaly` 来自最终 `DELIVERY_COMPLETE` 的锁存布尔值，可以在整场最终净重量为正、零或负时出现。后端不保存也不展示触发轮次、具体减少克数或中间重量；该标志只要求人工关注，不自行改变订单重量或钱包。
- `ownership.organizationUserUid` 只在 Web 中出现且必填。普通用户不接收后台操作者身份、`diagnosticDetails` 或完整 `revisions`，只接收 `review` 当前认定。
- `anomalies.category` 固定为 `USER/SYSTEM`。普通用户只看安全化 `code/message`；Web 的 `diagnosticDetails` 可以是可空结构化对象，但不得包含密钥、COS 对象凭据、协议原文或其他主体信息。
- `photos` 固定包含 `BEFORE_INNER/BEFORE_OUTER/AFTER_INNER/AFTER_OUTER` 四项。状态固定为 `UPLOAD_PENDING/AVAILABLE/PERMANENTLY_MISSING`；只有 `AVAILABLE` 时 `url` 非空，永久缺失时 `missingReason` 非空。Web 返回设备安全化缺失原因；普通用户使用面向用户的通用原因，不能暴露内部网络和设备诊断。
- `revisions.revisionType` 固定为 `INITIAL_REVIEW/CORRECTION`，决定固定为 `ORIGINAL_APPROVED/MODIFIED_APPROVED`。`operator.actorKind` 使用平台管理员/租户主体/工作人员等已经冻结的分型值，不返回数据库主键。
- 待审核的整场负净重量和负金额可以在原用户自己的订单详情中按原始事实显示，并明确标记“待人工审核”；它们在审核前仍不进入钱包。`negativeWeightAnomaly=true` 与整场净重量的正负正交。
- 后续机器可读 Schema 与契约测试必须覆盖 Web 必填用户归属和小程序整体省略该字段的两种形状；会话归属缺失样例必须验证其进入技术隔离而非订单接口。

### 4. 初审与纠错端点

普通 Web：

```http
POST /api/v1/web/organizations/{organizationCode}/delivery-orders/{deliveryOrderNo}/reviews
POST /api/v1/web/organizations/{organizationCode}/delivery-orders/{deliveryOrderNo}/corrections
Idempotency-Key: <UUIDv4>
```

平台镜像：

```http
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/delivery-orders/{deliveryOrderNo}/reviews
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/delivery-orders/{deliveryOrderNo}/corrections
Idempotency-Key: <UUIDv4>
```

按原数据通过：

```json
{
  "expectedRevisionNo": 0,
  "decision": "ORIGINAL_APPROVED",
  "finalWeightKg": null,
  "reason": null
}
```

修改后通过：

```json
{
  "expectedRevisionNo": 0,
  "decision": "MODIFIED_APPROVED",
  "finalWeightKg": "-1.25",
  "reason": "人工确认用户取走回收物"
}
```

请求约束：

- 初审只允许当前状态为 `PENDING` 且 `expectedRevisionNo=0`；成功创建修订号 1。
- 纠错只允许当前状态为 `APPROVED`，`expectedRevisionNo` 必须精确等于订单当前修订号；成功追加下一修订。
- `ORIGINAL_APPROVED` 要求 `finalWeightKg` 为 `null`，并使用可靠的原始业务重量。原始重量或锁定价格不可靠时不能选择该决定。
- `MODIFIED_APPROVED` 要求传入带符号、两位小数的千克字符串。最终金额由后端使用本单锁定四位小数单价并按 `HALF_UP` 舍入到分；请求不得包含最终金额。
- `finalWeightKg` 缺失、不是十进制定点字符串、精度不为两位或超出后端可安全表示的公共数值边界时，属于字段校验失败并返回 `400 COMMON.VALIDATION_FAILED`，不能用 `422` 混淆为业务资格失败。
- 最终认定重量还必须位于订单冻结的 `[-maxReviewAbsoluteWeightKg, +maxReviewAbsoluteWeightKg]` 内；该限制同样适用于 `ORIGINAL_APPROVED` 的原始重量。P0 新机构默认绝对值上限为 `"100.00"`kg；原始值越界时保留设备克值并形成系统异常，但原始业务重量/金额不可靠，审核员必须修改到范围内才能通过。后续配置变化不追溯旧订单。最终金额、纠错差额或整数分结果超出精确存储边界时同样整体拒绝，不能截断或环绕。
- 锁定价格无法恢复时，只允许以 `"0.00"` 形成最终 0 元认定，不能猜测价格或人工输入金额。
- `reason` 选填；即使为空，修订和操作审计仍必须记录操作者、时间、前后值及请求身份。

成功返回 `201 Created`，`data` 至少包含：

```json
{
  "deliveryOrderNo": "DO20260723...",
  "revisionUid": "593eea37-...",
  "revisionNo": 1,
  "reviewStatus": "APPROVED",
  "decision": "MODIFIED_APPROVED",
  "finalWeightKg": "-1.25",
  "finalAmountYuan": "-1.00",
  "walletDeltaYuan": "-1.00",
  "walletEffect": "APPLIED",
  "reviewedAt": "2026-07-23T08:40:15.123Z"
}
```

`walletEffect` 固定为 `APPLIED/NO_CHANGE`。本次差额为零时返回 `NO_CHANGE`；零金额或新旧认定金额相同不得制造虚假钱包变动。

### 5. 审核、资金与并发语义

1. 初审和纠错由 recycling 外层协调器先锁机构投递配置 head 取得本次资金变动线性化时的当前负余额停投阈值，再锁订单并复核当前修订，随后追加不可变修订、更新订单当前投影；有归属用户且本次金额差额非 0 时，才在同一数据库事务锁钱包、追加以修订唯一的资金明细、更新余额并执行负余额提现联动。会话/订单冻结的开始阈值只解释当时为何允许投递，不能用于后来审核或纠错放宽当前停投规则。差额为 0 时只提交修订和订单投影。任何必要步骤失败都整体回滚。
2. 初审前无论原始负数多大都不修改钱包；人工按原负数据通过或以负最终重量修改后通过时才扣款。审核人员认为设备异常时可以把最终重量改为 0 或其他可信值后通过。
3. 零重量是正常认定，不属于投递异常。四张照片缺失、补传中或永久缺失均不阻断审核、纠错或资金事务。
4. 已通过订单纠错没有时间上限。它不修改原始重量、原始金额、锁定单价、照片、归属或既有微信提现终态；新旧最终金额差额非 0 时只产生一次钱包效果，差额为 0 时不产生资金事实。
5. 操作者可以审核或纠错自己参与的业务记录。所有动作仍须通过实时能力和机构作用域校验。
6. `review.execute` 只允许首次审核；`delivery.correct` 只允许已通过订单纠错。拥有一个能力不能替代另一个能力。
7. 同一幂等键重试返回原修订结果；同一预期修订号的并发动作最多一个成功，其他返回版本冲突，不能静默套用到新版本。

主要错误包括：

```text
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
409 DELIVERY.REVISION_VERSION_CONFLICT
409 DELIVERY.ORDER_ALREADY_APPROVED
409 DELIVERY.ORDER_NOT_APPROVED
409 COMMON.IDEMPOTENCY_KEY_CONFLICT
400 COMMON.VALIDATION_FAILED
422 DELIVERY.ORIGINAL_DATA_UNRELIABLE
422 DELIVERY.FINAL_WEIGHT_OUT_OF_RANGE
422 DELIVERY.FINAL_AMOUNT_OUT_OF_RANGE
422 DELIVERY.LOCKED_PRICE_UNAVAILABLE
```

## I-025 钱包三项视图与真实资金明细

**已确认：钱包首页在一个一致数据库快照中返回待审核返现、可提现余额和提现处理中金额；待审核订单不伪造成钱包明细，Web 钱包查询使用独立能力。**

### 1. 查询端点

普通用户：

```http
GET /api/v1/miniapp/me/wallet
GET /api/v1/miniapp/me/wallet/entries?cursor={opaque}&limit={1..100}
```

普通 Web：

```http
GET /api/v1/web/organizations/{organizationCode}/organization-users/{organizationUserUid}/wallet
GET /api/v1/web/organizations/{organizationCode}/organization-users/{organizationUserUid}/wallet/entries?cursor={opaque}&limit={1..100}
GET /api/v1/web/organizations/{organizationCode}/wallet-entries?organizationUserUid={optional}&entryType={optional}&occurredFrom={optional}&occurredTo={optional}&sourceNo={optional}&cursor={opaque}&limit={1..100}
```

平台镜像：

```http
GET /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/organization-users/{organizationUserUid}/wallet
GET /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/organization-users/{organizationUserUid}/wallet/entries?cursor={opaque}&limit={1..100}
GET /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/wallet-entries?organizationUserUid={optional}&entryType={optional}&occurredFrom={optional}&occurredTo={optional}&sourceNo={optional}&cursor={opaque}&limit={1..100}
```

- 普通用户读取自己的钱包不需要 `wallet.read`；未绑定手机号仍可查看，但不能投递或提现。
- Web 端点要求 `wallet.read`，`user.read` 或 `delivery.read` 不自动授予钱包读取能力。
- 目标用户不在当前可见机构时统一返回 `404 RESOURCE.NOT_FOUND`。
- 机构级 `wallet-entries` 是 P0 后台筛选机构真实钱包流水的入口；筛选的用户、类型、左闭右开时间范围和来源单号都与当前机构作用域求交。每项额外返回 `organizationUserUid`，不得返回手机号、OpenID、钱包内部主键或其他机构数据。
- 所有钱包响应设置 `Cache-Control: no-store`；查询不要求幂等键。

### 2. 三项汇总

`GET .../wallet` 的 `data`：

```json
{
  "walletVersion": 12,
  "pendingRewardYuan": "3.20",
  "availableBalanceYuan": "-1.50",
  "withdrawalProcessingYuan": "2.00",
  "asOf": "2026-07-23T08:45:15.123Z"
}
```

- `pendingRewardYuan`：当前用户仍为 `PENDING`、原始金额可靠且严格大于 0 的投递订单原始金额之和。待审核负金额、零金额和金额未知不计入。
- `availableBalanceYuan`：钱包当前可提现余额，允许显示负数。
- `withdrawalProcessingYuan`：钱包当前非负提现冻结金额。
- `walletVersion`：钱包资金及闸门投影的并发版本；I-031 的后台人工调整使用它进行条件更新。普通用户不需要据此发起资金写操作。
- 三项必须由一个数据库语句或等价的同一一致性快照计算，不能分别拼接缓存或跨时间读取。`asOf` 表示该快照的服务端观察时间。
- 订单完成但尚未审核时只影响 `pendingRewardYuan`；首次审核后订单退出待审核汇总，非零最终金额通过真实钱包明细影响 `availableBalanceYuan`，零金额只完成订单认定。

### 3. 钱包明细

`GET .../organization-users/{organizationUserUid}/wallet/entries` 只返回已经实际改变该钱包可用或冻结投影的追加明细。每次在钱包锁内形成真实明细时分配从 1 开始、严格递增且不可回退的 `entrySequenceNo`；个人流水按该序号倒序形成不透明排他游标，并返回 `items` 和可空 `nextCursor`。随机 `entryUid` 只承担公开幂等身份，不能决定账本顺序。

机构级 `wallet-entries` 首屏在一致读取中冻结机构钱包明细提交可见序号作为 `snapshotHighWatermark`，后续页只读取不超过该水位的明细，再按 `occurredAt + organizationUserUid + entrySequenceNo` 稳定倒序。新事务无论携带何种业务发生时间，只要在首屏之后提交就取得更高水位，不会穿入后续页。水位、首屏 `asOf`、末项排序键和全部筛选摘要都封入防篡改游标；`limit` 默认 20、最高 100，改变筛选必须重新从首屏查询。

每项至少包含：

```json
{
  "entryUid": "b8ee9931-...",
  "entrySequenceNo": 12,
  "entryType": "DELIVERY_CORRECTION",
  "availableDeltaYuan": "-1.00",
  "processingDeltaYuan": "0.00",
  "availableBalanceAfterYuan": "-1.50",
  "withdrawalProcessingAfterYuan": "2.00",
  "sourceType": "DELIVERY_ORDER",
  "sourceNo": "DO20260723...",
  "occurredAt": "2026-07-23T08:40:15.123Z"
}
```

- P0 明细类型至少固定为 `DELIVERY_INITIAL_REVIEW`、`DELIVERY_CORRECTION`、`WITHDRAWAL_FREEZE`、`WITHDRAWAL_SUCCEEDED`、`WITHDRAWAL_RELEASED` 和 `MANUAL_ADJUSTMENT`；客户端显示文案不能依赖数据库内部数字值。
- `sourceType` 固定为 `DELIVERY_ORDER/WITHDRAWAL_ORDER/MANUAL_ADJUSTMENT`，并与 `entryType` 的合法来源组合校验。
- `availableDeltaYuan` 和 `processingDeltaYuan` 都是有符号金额。每项的变动后值必须与资金账本连续一致。
- 待审核返现没有钱包明细；用户通过 I-024 的订单列表查看其组成。不得为了让流水页面展示“待入账”而提前写一条可撤销的伪资金记录。
- `sourceNo` 始终非空，返回投递单号、提现单号或人工调整公开 UUID。`wallet.read` 允许看到该稳定来源号，但不因此授予订单、提现或调整详情权限；点击详情仍由对应资源接口独立鉴权。内部修订主键、钱包主键和数据库 `BIGINT` 不得返回。

## 后续细化边界

本章不定义清运准备/恢复、袋码、满溢人工重检、钱包人工调整、机构充值、提现、微信 APIv3、设备严重故障恢复、OneNet/COS 消息字段或 UART 帧。清运、袋、满溢和本批相关最小恢复已经由 I-026～I-030 闭合；钱包人工调整、机构充值、手动提现和真实微信渠道已由 I-031～I-035 闭合。下一批运营接口继续复用这里冻结的钱包三项口径、真实资金明细和负余额联动。
