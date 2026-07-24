# EcoBin P0 目标接口设计：设备资产、部署与配置（I-016～I-020）

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-016～I-020 已确认；2026-07-24 已按“一次投递会话一单”修订配置语义**
>
> 说明：本文件定义平台设备资产、机构设备部署、投口、运行视图、生命周期、经营开关、配置版本及配置应用的目标 HTTP 契约。所有接口继续遵守 I-001～I-015 的响应、可信作用域、公开身份、幂等、并发、权限和审计规则。

## 本章统一边界

1. 物理设备资产是平台资源，不直接带租户或机构归属；机构只能通过不可变的设备部署使用资产。普通 Web 路径从工作人员会话取得租户，平台协助路径必须显式携带目标租户和机构。
2. 本章使用三个稳定公开身份：物理资产使用不可变 `hardwareSn`，部署使用至少 128 位随机强度的 `deploymentCode`，投口使用 `deploymentCode + portNo`。任何响应和 URL 都不得暴露数据库 `BIGINT` 主键。
3. 所有创建、生命周期、经营开关、配置发布和重同步请求都携带 `Idempotency-Key`；修改既有对象还必须携带本章指定的预期版本。服务端在事务中重新鉴权、锁定设备资产根并校验最新状态，不能只依赖 Controller 进入时的读取结果。
4. 设备 Key 只配置在对应香橙派，后端 OneNet 下行使用部署环境注入的产品级 AccessKey。设备 Key、产品级 AccessKey、COS 临时凭证和任何秘密引用都不进入本章请求、响应、数据库设备资产、设备命令、可靠任务、日志或审计摘要。
5. 资产登记、部署创建、部署激活、经营开关开启、OneNet 接口受理、香橙派保存配置和 MCU 应用配置是六个不同事实。前一事实不得冒充后一事实。
6. `deliveryAllowed`、`cleaningAllowed` 和 `blockers[]` 是从租户/机构状态、部署、经营开关、配置应用、运行健康、安全、容量及占位实时计算的查询投影，不保存为可被其他写路径覆盖的 `available` 字段。真正创建作业时仍需在事务中重新检查。
7. 现有临时 OneNet 物模型和 UART 协议不能证明目标配置已经可靠落盘并同步 MCU。I-019/I-020 定义的是目标契约；正式 IoT/UART 契约及实现完成前，设备不得借旧 `property/set`、OneNet `code=0` 或旧 UART 单价帧通过激活门槛。
8. 一次有效扫码建立的投递 `sessionUid` 只在开始时校验并冻结投口、当前袋、单价、设备配置和投递配置。会话内“继续投递”只由设备本地再次打开投递门，不创建会话内云端分段作业、不重新授权、不采用后来发布的价格/袋/配置；普通满溢判断只在整场结束后执行，门卡滞、执行器故障、称重过载、烟雾等硬安全故障仍可阻止本地再次开门。

### 路径基准

本章机构级端点使用以下两个完整基准路径：

```text
普通租户：/api/v1/web/organizations/{organizationCode}/device-deployments
平台协助：/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/device-deployments
```

后文写成 `{deploymentBase}` 的端点，必须分别展开成上述普通租户和平台协助路径，不能让平台身份调用普通租户入口，也不能让普通工作人员在请求体自报租户。

### 权限目录与读取范围

本章新增三个稳定权限码，均允许 `TENANT` 和 `ORGANIZATION` 作用域：

| 权限码 | 能力 |
|---|---|
| `device.read` | 查看授权范围内的部署、投口、运行状态、配置版本和应用状态 |
| `device.manage` | 激活/停用部署以及开启/关闭后台经营开关 |
| `device.configuration.manage` | 发布设备完整配置并重同步当前配置应用；包含修改回收单价这一敏感动作 |

- 租户主体天然拥有本租户三项能力，机构负责人天然拥有本机构三项能力；普通工作人员继续由 I-014 的授权规则配置。P0 不建立可配置的平台权限目录：所有状态有效的平台管理员天然可以调用本章平台端点，但仍必须走显式平台路径、重新校验目标作用域并完整审计；平台管理员不成为租户工作人员。
- 集合列表要求 `device.read`。`device.manage` 可以读取其直接操作目标的安全部署、运行状态和版本；`device.configuration.manage` 可以读取其直接操作目标的配置与应用状态，但两者都不因此获得整个机构的列表权限。
- 租户级能力覆盖本租户全部机构；机构级能力只覆盖对应有效任职机构。平台路径始终重新校验目标租户、机构和资源实际归属。
- 配置发布、价格变化、部署激活/停用、经营开关和重同步全部写操作记录操作者、目标、前后版本、结果和可空原因；审计不复制完整配置正文或任何凭证。

### 版本与并发矩阵

| 资源 | 查询返回 | 修改请求 | 成功后的版本语义 |
|---|---|---|---|
| 物理资产 | `version` | 资产创建不需要不存在对象的版本；部署创建携带 `expectedAssetVersion` | 资产创建初始 `version=0`；直接部署使资产版本恰好 `+1` |
| 设备部署 | `version` | 激活携带 `expectedVersion + expectedConfigurationVersion`；停用和经营开关携带 `expectedVersion` | 每个成功命令使部署版本恰好 `+1` 并返回新值 |
| 运行投影 | `runtimeVersion` | Web 不直接修改；可信设备事实按自己的并发规则更新 | 每次实际投影变化递增；只供观察，不代替命令中的部署版本 |
| 配置版本 | 不可变 `versionNo` | 发布携带 `expectedLatestVersion`，首次发布为 `0` | 服务端分配下一连续版本号；旧版本不修改 |
| 配置应用 | `version` | 重同步携带应用 `expectedVersion` | 可靠受理重同步或可信应用状态推进时递增并返回新值 |

## I-016 平台物理设备资产

**已确认：M0 资产登记只是 EcoBin 本地的平台库存事实，不在 HTTP 请求中创建 OneNet 设备、写设备密钥或假装验证设备在线。**

### 1. 平台资产接口

```http
GET  /api/v1/web/platform/device-assets
POST /api/v1/web/platform/device-assets
GET  /api/v1/web/platform/device-assets/{hardwareSn}
```

创建请求：

```json
{
  "hardwareSn": "SN-001",
  "modelCode": "ECOBIN-V1",
  "productionBatch": "2026-07",
  "expectedPortCount": 4
}
```

- `hardwareSn` 是大小写敏感、全平台唯一且创建后不可修改的公开身份；`expectedPortCount` 必须为 `1..6`。型号必填，生产批次可空。
- P0 使用当前部署环境配置的单一 OneNet 产品 ID，并固定 `oneNetDeviceName=hardwareSn`。产品 ID 不属于某台资产，设备名又可由硬件 SN 确定性推导，因此二者都不重复保存到资产表；详情可以返回标明为“当前计算值”的有效 OneNet 映射，不能把它解释成资产创建时固化的历史字段。
- 该产品 ID 是 P0 部署纪元配置，不提供运行时热切换接口。以后更换 OneNet 产品必须作为受控迁移重新验证全部资产映射和迟到消息边界，不能只改配置后让历史身份静默改义。
- OneNet 设备由运维人员预先在控制台建立，设备 Key 直接配置到对应香橙派；EcoBin 后端只使用产品级 AccessKey 执行下行，不需要设备 Key。
- 创建事务只写本地 `IN_STOCK` 资产和成功审计，初始 `version=0`，返回 `201`。事务内外都不调用 OneNet，响应不声称 OneNet 设备存在、在线或凭证正确。
- OneNet 公开身份是否真实匹配，只能由部署后的可信上行、配置下发与应用证明共同验证：可信消息的产品 ID 必须等于当前系统配置，设备名必须映射为该资产的 `hardwareSn`；没有这些证据的设备不能通过 I-018 激活。
- 列表使用普通管理分页，支持硬件 SN、型号、批次和资产状态筛选；详情返回投口数量、资产状态、当前部署安全摘要、版本、时间，以及可选的当前计算 OneNet 映射，不返回任何密钥、秘密引用或内部主键。
- M0 不提供资产资料修改、删除、分配、维修、报废或密钥轮换端点。相同硬件 SN 使用其他操作号再次创建返回 `409 DEVICE.ASSET_ALREADY_EXISTS`，不能覆盖原资产。

## I-017 M0 受控直接部署

**已确认：M0 由平台把一台库存资产直接建立为指定机构的调试中部署；部署、当前资产槽、全部投口和初始安全投影在一个本地事务中成立。**

### 1. 创建与查询接口

```http
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/device-deployments

GET  /api/v1/web/organizations/{organizationCode}/device-deployments
GET  /api/v1/web/organizations/{organizationCode}/device-deployments/{deploymentCode}

GET  /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/device-deployments
GET  /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/device-deployments/{deploymentCode}
```

普通租户没有部署创建端点，不能只凭硬件 SN 认领平台库存。创建请求为：

```json
{
  "hardwareSn": "SN-001",
  "expectedAssetVersion": 0
}
```

### 2. 原子创建结果

服务端锁定物理资产后，在同一事务完成：

1. 重新校验目标租户、机构存在，资产为 `IN_STOCK`、版本匹配且没有当前部署槽；
2. 生成至少 128 位随机强度、全局唯一且不可变的 `deploymentCode`；
3. 创建生命周期为 `COMMISSIONING`、后台经营开关为关闭的部署；
4. 创建资产当前部署槽，并按资产声明的 `1..N` 投口编号一次性创建全部投口；
5. 创建整机和各投口的 `UNKNOWN` 运行投影，并设置只能由首次合格验收解除的初始调试安全锁；
6. 把资产推进为 `IN_USE`、资产版本 `+1`，写入成功审计。

任一步失败都不得留下部署、投口、运行投影、当前槽或部分资产状态。成功返回 `201`，至少包含 `deploymentCode`、租户/机构安全摘要、资产摘要、生命周期、经营开关、投口数量、部署版本、资产新版本和创建时间。

- 部署创建不建立默认配置；第一版配置必须通过 I-019 单独发布并取得真实应用证明。
- 用户二维码载荷固定为结构化的 `tenantCode + deploymentCode`，不包含硬件 SN、OneNet 身份、数据库主键、设备 Key 或安装凭证。二维码不是授权凭证，扫码后仍需校验 AppID、机构、部署和用户资格。
- 部署列表使用普通管理分页，支持生命周期、经营开关、硬件 SN、在线状态和配置应用状态筛选。普通工作人员只能看到能力覆盖机构。
- 资产、租户、机构、公开码和投口编号创建后不可修改。M0 不开放 `PENDING_INSTALL`、安装码、资产分配、租户认领、调拨、迁址、替换或结束部署；这些属于 M1 的显式“结束旧部署＋创建新部署”流程。

主要错误包括 `DEVICE.ASSET_NOT_FOUND`、`DEVICE.ASSET_NOT_IN_STOCK`、`DEVICE.ASSET_ALREADY_DEPLOYED` 和 `COMMON.VERSION_CONFLICT`。

## I-018 生命周期、经营开关与运行视图

**已确认：部署生命周期、后台经营意图和实时作业资格是三个独立事实；激活及经营开关只能改变本地管理状态，不能伪造远程设备动作或清除运行故障。**

### 1. 精确端点

以下后缀同时适用于本章定义的普通租户和平台 `{deploymentBase}`：

```http
GET  {deploymentBase}/{deploymentCode}/ports
GET  {deploymentBase}/{deploymentCode}/runtime
GET  {deploymentBase}/{deploymentCode}/ports/{portNo}/runtime

POST {deploymentBase}/{deploymentCode}/activations
POST {deploymentBase}/{deploymentCode}/deactivations
POST {deploymentBase}/{deploymentCode}/business-switch/enablements
POST {deploymentBase}/{deploymentCode}/business-switch/disablements
```

查询响应明确分开展示：

- 部署生命周期、后台经营开关和部署版本；
- 最新已发布配置、最新已应用配置的版本与摘要；
- OneNet/香橙派/MCU 在线及协议版本、最后可信观察时间；
- 整机安全、严重安全锁、相机、本地存储和时钟状态；
- 投递门的真实门控/门磁状态，称重、红外、烟雾健康，清运电磁阀的当前通断状态及由此推定的清运门状态，以及 recycling 模块提供的当前容量/满溢安全摘要；清运门推定状态不得标成物理门磁检测结果；
- 当前整机占位安全摘要；
- 实时计算的整机及投口 `deliveryAllowed`、`cleaningAllowed` 和稳定 `blockers[]`。

核心阻断码至少包括 `TENANT_DISABLED`、`ORGANIZATION_DISABLED`、`DEPLOYMENT_NOT_ENABLED`、`BUSINESS_SWITCH_DISABLED`、`CONFIGURATION_NOT_APPLIED`、`EDGE_OFFLINE`、`MCU_OFFLINE`、`PROTOCOL_INCOMPATIBLE`、`SAFETY_LOCKED`、`DOOR_NOT_CLOSED`、`PORT_DISABLED`、`PORT_SENSOR_UNHEALTHY`、`PORT_FULL`、`CURRENT_BAG_MISSING`、`WEIGHT_BASELINE_MISSING`、`BASELINE_REMEASUREMENT_ACTIVE`、`PORT_CLEAN_OPERATION_ACTIVE` 和 `DEVICE_BUSY`。当前袋对全部投递模式必需；重量基准缺失只在使用重量参与满溢判断时阻断。查询是观察快照，后续投递/清运事务仍须重新检查。

I-026 进一步收窄 `cleaningAllowed` 与 `deliveryAllowed` 的差异：投口已满、旧袋绑定/旧基准缺失、已经终结的容量检测失败或投递结果待处理不会单独禁止真实清运，因为完成换袋可以建立新的袋与检测 gate；尚未终结的满溢检测或空袋基准重测只形成短暂冲突，清运不取消或改写它。整机/门安全锁、已知严重称重或本地存储故障、离线/协议不兼容、整机占位和未终结清运仍禁止。运行视图必须分别计算两种资格，不能复用一份 blockers 后把 `PORT_FULL` 同时套到清运。

### 2. 激活和停用

激活请求：

```json
{
  "expectedVersion": 0,
  "expectedConfigurationVersion": 1,
  "acceptanceConfirmed": true,
  "reason": null
}
```

`COMMISSIONING/DISABLED → ENABLED` 必须在设备资产锁根下同时满足：

1. 租户、机构和资产当前部署关系有效；
2. 最高已发布配置恰好等于请求的 `expectedConfigurationVersion`，并已经 `APPLIED`，设备报告的版本和摘要精确匹配；
3. 已经收到该资产 OneNet 产品/设备公开身份的可信上行，不能只相信资产建档字段；
4. 香橙派程序、MCU 固件、配置协议和 UART 协议达到 M0 最低兼容版本；
5. 香橙派与 MCU 在线，关键组件健康，全部投递门由真实传感器确认关闭，清运门电磁阀已断电；后者只能推定清运门关闭，不能作为物理关门证明；
6. 不存在投递或清运整机占位；
7. 操作者明确提交 `acceptanceConfirmed=true`。

首次从 `COMMISSIONING` 激活可以在同一事务解除 `INITIAL_COMMISSIONING` 调试锁并保存验收确认审计，但不能清除设备运行后形成的严重安全故障锁。普通健康心跳、再次激活和经营开关都无权清除严重锁；其恢复端点在后续设备恢复批次定义。

激活成功只把生命周期改为 `ENABLED`，经营开关仍保持关闭。停用请求携带 `expectedVersion + reason`，只允许 `ENABLED → DISABLED`，并在同一事务关闭经营开关。两种命令成功均返回 `200` 和新部署版本。

停用不会取消已可靠受理的作业、撤销已发送命令、释放当前占位或拒绝迟到结果；它只阻止后续新作业，原作业继续按冻结作用域和配置收敛。

### 3. 后台经营开关

经营开关请求携带 `expectedVersion + reason`。

- 开启要求部署已经 `ENABLED`，租户/机构有效，最高配置精确应用，协议兼容，设备在线，全部投递门状态已知且真实关闭，清运电磁阀断电，并且没有阻断安全锁或整机占位。清运门在该判断中只有“断电后推定关闭”，不能伪装成真实门状态。容量已满只会阻断对应投口投递，不改变整机经营意图。
- 关闭只把经营意图设为关闭，不修改生命周期、设备健康、配置、容量或已有作业。重新开启仍需按届时真实状态完整检查。
- 开关变化都是本地同步事务，成功返回 `200`；响应不表示设备执行了远程命令。使用新操作号重复设置相同状态返回明确 `409`，同一 `Idempotency-Key` 重试仍返回首次结果。

主要错误包括 `DEVICE.DEPLOYMENT_NOT_ACTIVATABLE`、`DEVICE.CONFIGURATION_NOT_APPLIED`、`DEVICE.PROTOCOL_INCOMPATIBLE`、`DEVICE.SAFETY_LOCKED`、`DEVICE.DEVICE_BUSY`、`DEVICE.DEPLOYMENT_ALREADY_ENABLED`、`DEVICE.DEPLOYMENT_ALREADY_DISABLED`、`DEVICE.BUSINESS_SWITCH_ALREADY_ENABLED` 和 `DEVICE.BUSINESS_SWITCH_ALREADY_DISABLED`。

## I-019 完整配置发布

**已确认：设备配置只能以覆盖整机和全部投口的不可变完整快照发布；发布本地事务可靠受理下发意图，但只有 I-020 的精确设备证明才能形成应用事实。**

### 1. 配置端点

```http
GET  {deploymentBase}/{deploymentCode}/configuration-versions
GET  {deploymentBase}/{deploymentCode}/configuration-versions/{versionNo}
POST {deploymentBase}/{deploymentCode}/configuration-releases
```

版本列表按 `versionNo` 倒序使用稳定键集游标：`beforeVersionNo` 可空且表示不包含该版本、`limit` 默认 20 且最大 100；响应返回 `items` 和可空 `nextBeforeVersionNo`，为空表示没有下一页。详情返回完整不可变快照、规范摘要、发布者安全摘要、发布时间和对应应用摘要，不返回设备密钥、临时凭证或可靠任务执行载荷。

### 2. 完整发布请求

```json
{
  "expectedLatestVersion": 3,
  "reason": null,
  "locationCorrectionConfirmed": false,
  "device": {
    "displayName": "A区1号设备",
    "address": "A区北门",
    "longitude": "113.123456",
    "latitude": "23.123456",
    "edgeHeartbeatIntervalMs": 30000,
    "edgeHeartbeatMissThreshold": 3,
    "mcuHeartbeatIntervalMs": 5000,
    "mcuHeartbeatMissThreshold": 3,
    "doorCloseRetryLimit": 3,
    "continueDeliveryWaitMs": 30000,
    "negativeWeightThresholdGram": 500
  },
  "ports": [
    {
      "portNo": 1,
      "displayName": "一号投口",
      "enabled": true,
      "unitPriceYuanPerKg": "0.8000",
      "fullnessMode": "INFRARED_OR_WEIGHT",
      "fullnessWeightKg": "50.00",
      "deliverySettleDelayMs": 3000,
      "fullnessInitialDelayMs": 3000,
      "fullnessRecheckDelayMs": 10000,
      "doorAutoCloseTimeoutMs": 60000
    }
  ]
}
```

- `expectedLatestVersion=0` 表示首次发布；其他值必须与当前最高发布版本一致。
- `ports` 必须恰好覆盖资产声明的 `1..N`，不能缺失、重复、增加或修改投口编号。所有时长使用非负毫秒整数，并须落在后续机器可读配置 Schema 声明的设备安全范围内。
- `continueDeliveryWaitMs` 是每次投递门真实关闭并完成本地称重后，设备屏幕等待当前用户选择“继续投递”的本地窗口；默认 `30000` 毫秒，由香橙派按本会话冻结配置使用单调时钟执行。用户及时选择后，设备在硬安全条件仍成立时直接在同一 `sessionUid` 下再次开投递门；该按钮事件、再次开门、轮次称重和中间照片均不上云，也不创建新的后端授权或订单。用户选择结束或窗口届满时才拍摄最终照片并生成整场唯一完成事件。
- `negativeWeightThresholdGram` 是设备本地负重量异常锁存阈值，单位为克，必须为正整数，默认 `500`。同一投递会话内任一轮次的“本轮关门后重量－本轮开门前重量”小于等于该阈值的负值（即减少量大于等于阈值）时，设备把 `negativeWeightAnomaly` 锁存为 `true` 直至整场结束。最终只在唯一 `DELIVERY_COMPLETE` 中上报布尔标志，不上报中间轮次重量或具体减少值；该标志不阻止继续投递，也不改变整场净重量结算。
- 单价遵守 I-002 的四位小数元/千克字符串且必须严格大于零；暂停免费回收使用 `enabled=false`，不能把价格设为零。一次投递会话冻结首次开门前已经应用的版本、当前袋和单价，后续价格、袋或配置变化不追溯修改该会话。
- 满溢模式只允许 `INFRARED_ONLY`、`WEIGHT_ONLY`、`INFRARED_OR_WEIGHT`；重量阈值必须大于零。配置应用本身不触发红外或重量采样，也不重新解释、清除现有满溢事件或严重安全锁；新规则只供后续真实投递后检测使用。
- 后端固定的 60 秒开始授权有效期不是租户设备参数，不能通过该请求修改。它只限制 `START_DELIVERY_SESSION` 能否首次执行；设备在期限内可靠受理并开始首轮后，整场继续投递不再依赖云端期限或在线重新授权。30 秒选择窗口及负重量阈值来自本会话冻结配置。若以后增加整场最长时限，必须作为新的明确配置同时进入后端快照、命令摘要和设备单调计时，不能复用已废弃的分段结果期限或云端继续处理任务。
- 首次配置以外修改地址或坐标时必须提交 `locationCorrectionConfirmed=true` 并审计；它只表示文字纠正或小范围坐标修正。实际迁址返回 `422 DEVICE.DEPLOYMENT_RELOCATION_NOT_SUPPORTED`，不能借配置修改历史机构或部署地点。
- 服务端按固定 Schema、字段顺序、数值格式及 `portNo` 排序规范化内容后计算 SHA-256。与当前最高版本内容完全相同的新发布返回 `422 DEVICE.CONFIGURATION_UNCHANGED`，不制造空版本。

### 3. 发布事务与新旧版本

发布事务按设备资产锁根重新鉴权并检查版本，在一个事务中创建：

1. 下一连续且不可变的配置版本及全部投口快照；
2. 唯一 `PENDING` 配置应用；
3. 以 `applicationUid` 为目标的稳定设备配置命令；
4. 唯一 OneNet 可靠下发任务；
5. 成功操作审计。

事务提交后才允许可靠任务调用 OneNet。成功返回 `202`，其中 `operationId` 等于本次 `Idempotency-Key` 对应的操作号，`resourceId` 等于 `applicationUid`；同时返回 `applicationUid`、`versionNo`、`contentSha256`、`status=PENDING`、`dispatchState=PENDING`、`statusUrl` 和服务端建议的 `recommendedPollAfterMs`，并用 `Location` 指向 I-020 的应用查询资源。这里的 `status` 始终指配置应用状态，任务状态只使用 `dispatchState`。

- 发布不要求设备当前在线，也不取消已开始的投递/清运；活动投递会话和清运操作继续使用各自冻结的旧配置。最高发布版本尚未 `APPLIED` 时，所有新投递和新清运都被 `CONFIGURATION_NOT_APPLIED` 阻断。
- 配置只允许在设备无作业、全部投递门由真实传感器确认关闭且清运电磁阀断电时真正切换。清运门的断电状态只能提供关闭推定，不能声称已检测到物理关门；香橙派可以先可靠保存新版本，但 MCU 同步和最终 `APPLIED` 必须继续满足该安全边界。
- 允许发布更高版本纠正一个尚未完成的错误版本。新版本提交后，旧应用通过“其版本低于最高期望版本”派生 `latestDesired=false` 和 `superseded=true`，但不改写它真实的 `PENDING/EDGE_SAVED/APPLIED/FAILED` 应用状态；旧任务仍为 `PENDING/BLOCKED` 时按权威最新版本安全进入 `CANCELLED` 并停止后续外调，已经 `DONE/CANCELLED` 的任务保留其真实终态。版本取代不是设备失败，也不增加第五种应用状态。
- 若旧任务可能已经外调，系统不能宣称旧版本从未到达设备。设备侧必须拒绝低于本地最高已接受版本的配置；可信旧版本迟到证明仍保存和归并，但旧版本即使真实应用过也不能重新成为当前期望版本，业务仍等待最高版本 `APPLIED`。
- 回滚通过把旧内容重新作为更高版本发布完成，不原地修改、删除或把版本号倒退。

主要错误包括 `DEVICE.CONFIGURATION_PORT_SET_INVALID`、`DEVICE.CONFIGURATION_VALUE_INVALID`、`DEVICE.CONFIGURATION_UNCHANGED`、`DEVICE.DEPLOYMENT_RELOCATION_NOT_SUPPORTED` 和 `COMMON.VERSION_CONFLICT`。

## I-020 配置应用与同版本重同步

**已确认：配置应用状态只由可信设备证明和明确的本地取代决定推进；OneNet 传输受理、网络重试结果和 Web 人工操作都不能伪造边缘或 MCU 已完成。**

### 1. 查询与重同步端点

```http
GET  {deploymentBase}/{deploymentCode}/configuration-applications/{applicationUid}
POST {deploymentBase}/{deploymentCode}/configuration-applications/{applicationUid}/resynchronizations
```

`GET` 返回 HTTP `200`，至少包含：

```json
{
  "applicationUid": "...",
  "versionNo": 4,
  "contentSha256": "...",
  "status": "PENDING",
  "version": 2,
  "latestDesired": true,
  "superseded": false,
  "deviceReportedVersionNo": null,
  "deviceReportedSha256": null,
  "edgePersistedAt": null,
  "mcuSyncedAt": null,
  "appliedAt": null,
  "lastFailureCode": null,
  "lastFailedAt": null,
  "dispatchState": "PENDING",
  "recommendedPollAfterMs": 3000,
  "nextActions": ["WAIT"]
}
```

`dispatchState` 来自唯一可靠任务的安全投影，只解释下发执行情况；它与配置应用 `status` 不是同一状态机。

`superseded` 是派生展示值，恒等于 `!latestDesired`，不单独落库，也不作为第五种应用状态。

- `nextActions` 只返回稳定值：正常收敛中为 `WAIT`；当前最高版本允许人工唤醒时为 `RESYNCHRONIZE`；设备明确拒绝且只能修改内容时为 `PUBLISH_NEW_CONFIGURATION`；已被取代时为 `VIEW_LATEST_CONFIGURATION`；没有后续动作时为空数组。
- `recommendedPollAfterMs` 只在仍建议轮询时返回，客户端不得用固定高频轮询覆盖服务端建议；`APPLIED`、不可恢复 `FAILED` 或已被取代时返回 `null`。
- `status=FAILED` 时，`lastFailureCode/lastFailedAt` 是导致当前失败的最近可信证据；以后精确成功证明使状态前进时，这两个诊断字段仍保留，客户端必须以 `status` 判断当前结果，不能因历史失败字段非空继续显示为失败。

### 2. 应用状态及证明

```text
PENDING → EDGE_SAVED → APPLIED
   │          │
   └──────────┴────→ FAILED

FAILED ──同版本重同步──→ PENDING
FAILED ──精确且更新的可信证明──→ EDGE_SAVED / APPLIED
```

| 状态 | 唯一含义 |
|---|---|
| `PENDING` | 中心已可靠建立配置意图，但尚未取得完整应用证明 |
| `EDGE_SAVED` | 可信设备事件证明香橙派已把相同版本、Schema 和摘要可靠落盘 |
| `APPLIED` | 在无在途作业、全部投递门真实关闭且清运电磁阀断电的前提下，必要 MCU 配置也已同步同一版本和摘要；清运门仅有关闭推定 |
| `FAILED` | 设备明确 NACK、边缘持久化失败或 MCU 同步失败；失败码必须区分原因。版本被取代是正交的 `latestDesired=false`，不是失败 |

- OneNet HTTP `code=0`、可靠任务一次发送成功、设备在线、旧 `thingServiceReply accepted=true` 或串口写成功都不能推进 `EDGE_SAVED/APPLIED`。
- 可信应用事件必须携带稳定 `applicationUid`、配置版本、Schema、内容摘要、设备持久事件 ID/序号及明确阶段；来源 OneNet 产品/设备身份必须映射到原物理资产和部署。相同事件同摘要幂等命中，同 ID 不同摘要进入隔离。
- 后端归并证明时按资产锁根重新检查部署、当前最高期望版本、占位、投递门真实关闭状态和清运电磁阀断电状态。`APPLIED` 只表示该版本确实应用过，不增加清运门物理关门证明；只有最高期望版本 `APPLIED` 才能解除新作业的配置阻断。
- 精确可信且设备持久序号更新的迟到证明可以把 `PENDING/EDGE_SAVED/FAILED` 推进到真实的更高阶段；`APPLIED` 不回退。全部失败证据继续保存在只追加命令事件中，应用查询的 `lastFailureCode/lastFailedAt` 也不因后来成功而抹除。非当前版本的证明只更新其历史应用事实，绝不能降低设备当前期望版本、重启其已取消任务或开放业务。
- 可靠任务自动重试耗尽时只把 `dispatchState` 变为 `BLOCKED` 并产生聚合告警，配置应用保持原真实状态。技术失败不能顺带把应用、部署或经营开关伪造成失败/停用。
- 当前最高期望应用处于 `PENDING/EDGE_SAVED` 时，其 `ENSURE_DEVICE_CONFIGURATION` 任务必须保持 `PENDING`，按策略复送或复检，不能因为 OneNet `code=0` 就进入 `DONE`。应用精确 `APPLIED` 后任务才可 `DONE`；设备明确失败时任务可在保存真实失败后 `DONE`，等待受审计重同步；版本被取代时旧任务进入 `CANCELLED`；自动尝试上限耗尽进入 `BLOCKED`。`应用=PENDING/EDGE_SAVED + dispatchState=DONE/CANCELLED` 对当前最高版本属于不变量错误，必须告警而不是永久返回 `WAIT`。
- Web 无权直接提交 `EDGE_SAVED`、`APPLIED`、`FAILED`、设备报告版本或摘要；这些字段只由可信 OneNet inbox 处理器写入。I-019 只改变“当前最高期望版本”和旧任务执行资格，不伪造应用状态。

### 3. 同版本重同步

重同步请求：

```json
{
  "expectedVersion": 2,
  "reason": null
}
```

- 只允许对当前最高期望版本执行，并且该应用必须处于可恢复 `FAILED`，或其唯一可靠任务已经 `BLOCKED`。已经 `APPLIED`、已被更高版本取代或仍在正常执行的应用不能重同步。
- 受理事务复用原 `applicationUid`、配置版本、设备命令和 `ops_reliable_task`，递增任务 `wakeVersion` 并恢复为待执行；可恢复 `FAILED` 同时回到 `PENDING`。后续领取只新增 `ops_task_attempt`，不得创建第二个应用、命令或任务。
- 可靠受理返回 `202`：`operationId` 等于本次重同步操作号，`resourceId` 等于原 `applicationUid`，`status` 返回受理后的应用状态，`dispatchState` 返回原任务恢复后的状态，并返回 `recommendedPollAfterMs` 和同一个 `statusUrl`；`Location` 仍指向原应用资源。同一 `Idempotency-Key` 重试返回首次结果；版本或恢复资格变化返回 `409`。

主要错误包括 `DEVICE.CONFIGURATION_APPLICATION_NOT_FOUND`、`DEVICE.CONFIGURATION_APPLICATION_SUPERSEDED`、`DEVICE.CONFIGURATION_ALREADY_APPLIED`、`DEVICE.CONFIGURATION_RESYNC_NOT_ALLOWED` 和 `COMMON.VERSION_CONFLICT`。

### 4. 当前实现阻断边界

当前硬件协议仍存在以下目标契约缺口：

- OneNet 临时 `property/set(unitPrice)` 没有中心配置版本、规范摘要或 `applicationUid`；
- 当前 OneNet `code=0` 只证明平台受理转发，不能证明香橙派可靠保存；
- 当前临时 UART 单价只能表达一位小数，且缺少完整配置版本、摘要、命令序号、CRC 和独立 ACK/重试；
- 清运及多投口还没有统一到能证明 MCU 同步完成的正式协议。

因此这些旧消息不得写入 `dev_config_application` 的 `EDGE_SAVED/APPLIED` 字段。I-041～I-050 已分别冻结 OneNet、边缘 SQLite 和 UART 语义，其中 UART 配置应用以完整 `contentSha256` 绑定云端版本，并以独立 `mcuPayloadSha256` 证明必要 MCU 子集经过完整分段校验和原子切换；下一批仍须把它们落实为机器可读 Schema、跨语言样本和真机契约测试。代码、固件及测试完成前，I-018 激活必须保持阻断。

## 后续细化边界

本章不重复定义普通用户扫码开门、投递会话、审核纠错、清运、袋码、满溢恢复、设备严重故障人工恢复、OneNet Topic/事件字段、COS 凭证、边缘 SQLite 表或 UART 字节帧。后续已经由 I-021～I-050 分章承接本章冻结的 `deploymentCode`、`applicationUid`、版本、摘要和分层成功语义；实现仍须使用同一机器来源生成/校验跨端契约。
