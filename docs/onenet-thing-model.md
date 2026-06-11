# EcoBin OneNet 物模型设计

> 面向中国移动 OneNet 平台的设备物模型定义，供 OneNet 控制台导入与设备固件、后端对接共同遵循。
> 本文是「物模型字段口径」的单一来源：物模型功能点标识符与后端 `/api/iot/**` DTO 字段一一对齐，避免数据转发时再做字段映射。
> 初版：2026-06-09。OneNet 凭证（product_id / access_key）与真实下发 API 待到位（见 `open-items.md` §2）。

---

## 0. 设计原则与前提

1. **身份字段不进物模型。** 设备序列号 `sn` 是设备身份，对应 OneNet 的 `device_name`（注册设备时令其等于 `biz_device.sn`）。OneNet 数据转发报文头自带设备身份，后端据此反查租户，因此属性/事件里**不再重复携带 `sn`**。
2. **标识符即后端字段。** 物模型功能点 `identifier` 直接采用后端 DTO 的字段名（驼峰），使 OneNet 推送的 `payload` 解包后可直接绑定到现有 `DeliveryReportRequest` / `CleanGrossRequest` / `CleanTareRequest`。
3. **开门是服务不是属性。** 开投递投口、开清运门是带参数、需回执的一次性动作，建模为**服务（service）**，不建模为可写属性。
4. **业务上报是事件不是属性。** 投递完成、清运毛重、去皮是离散业务事件（一次一条、需幂等），建模为**事件（event）**；持续性的设备健康指标（在线、电压、满溢）才建模为**属性（property）**。
5. **精简范围。** 本期不纳入小智参考文档里的压缩机、相机抓拍、湿度、红外等功能点，聚焦：投递、清运（去皮链）、基础告警、运维健康。后续需要再扩。

---

## 1. 数据流向

```
设备固件 ──MQTT(物模型 Topic)──► OneNet 平台
                                     │
                                     │ 北向：消息队列 MQ 订阅（首选，后端作消费者拨出去，免公网）
                                     │       HTTP 推送（备选，需后端有公网 IP）
                                     ▼
                          后端 MQ 消费者 / POST /api/iot/onenet/notify   ◄── 待建：OneNet 报文适配入口
                                     │ 解包 notify_type + payload，按事件标识符分发
                                     ├─► deliveryComplete ─► 现有投递两阶段 Service
                                     ├─► cleanGross ───────► CleanOrderService.reportGross
                                     ├─► cleanTare ────────► CleanOrderService.reportTare
                                     ├─► photoReport ──────► PhotoNotifyService（按 refToken 回填 URL）
                                     └─► 属性/告警 ────────► DeviceStatusService（待补）

后端下发：DeviceCommandService → OneNetClient → OneNet 服务调用 API → 设备
          ├─ openDeliveryDoor   （投递开投口，对应 sendOpenDoor；下发时带 cosToken）
          └─ openCleanDoor      （清运开门，对应 sendOpenCleanDoor；下发时带 cosToken）
```

**关键决策**：
- 北向（设备→后端）**以消息队列 MQ 订阅为主**：后端作消费者主动连 OneNet，免公网、可靠不丢、天然削峰（设备多时优势明显）。HTTP 推送仅作「有公网 IP 时」的备选；二者解包后复用同一批 Service，互不影响。
- 保留现有 `/api/iot/clean/gross`、`/api/iot/clean/tare`、投递上报、`/api/iot/photo/**` 端点给「设备直连联调」用。

### 1.1 图片链路（抓拍 4 张：开门前/关门后 × 箱内/箱外）

图片二进制由**设备直传腾讯云 COS**（设备↔COS 公网直连，不经后端、不占后端带宽）。
**照片的对象 key 由后端开门时确定性生成**：开门那一刻后端已握有 `deliveryToken`/`cleanOrderId`，
能算出 4 个 key、立刻把 4 个完整 URL 预存进订单，并把凭证 + 4 个 key 随开门命令下发给设备。
设备按槽位直传到指定 key 即可，**无需回传 URL**：

```
① 后端：开门即按 token 生成 4 个 key，预存订单 photo_* 的 4 个完整 URL（baseUrl + key）
② 后端 ──openDeliveryDoor/openCleanDoor(input 带 cosToken：凭证 + 4 个 key)──► OneNet ──► 设备
        （COS 临时密钥 + key 搭车开门命令下发，设备开门即拿到，无往返、无需独立续发）
③ 设备：开门前抓拍 2 张 → 关门后抓拍 2 张 → 用凭证按 cosToken 里的 4 个 key 直传 COS（完）
```

> key 形如 `{sn}/{doorIndex}/{token}/<slot>.jpg`，token = 投递 `deliveryToken` / 清运 `cleanOrderId`
> （随开门命令下发，与订单一一对应，避免同投口多单互相覆盖）。
> **兜底**：后端开门即存 URL、不校验对象是否真上传成功；设备若没传上，前端加载出 404 显示占位图。

---

## 2. 属性（Property）— 设备 / 投口状态

持续性、可周期上报、平台可读。**按表职责分离**为两组，分别落两张表：
- **设备级**（配置 / 物理 / 健康）→ `biz_device_status`（`DeviceStatus` 实体，现有实体缺上报入口，借此重整并补齐）。
- **投口级**（重量 / 投递 / 告警实时）→ `biz_door_status`（**本次新增表**），每投口一条。

### 2.1 设备级属性 → `biz_device_status`

| identifier | 名称 | dataType | 读写 | 对应后端 | 说明 |
|------------|------|----------|------|---------|------|
| `online` | 在线状态 | bool | r | DeviceStatus.online | OneNet 自带在线判定，可二选一 |
| `voltage` | 电压 | float (V) | r | DeviceStatus.voltage | 整机供电 |
| `rssi` | 信号强度 | int32 | r | **新增 DeviceStatus.rssi** | 运维 |
| `fwVersion` | 固件版本 | string | r | **新增 DeviceStatus.fwVersion** | 运维 |

> 去掉原 `totalWeight`：整机总重由各投口 `weight` 聚合得出，不单独落库。
> 原 `spillAlarm` / `smokeAlarm` 是投口级实时状态，移入 `doorStates`（见 §2.2），不再挂设备级。

### 2.2 投口级属性 `doorStates`（array of struct，size ≤ 6）→ `biz_door_status`

| 子字段 | 名称 | dataType | 对应后端 |
|--------|------|----------|---------|
| `doorIndex` | 投口号 | int32 | DoorStatus.doorIndex |
| `weight` | 当前即时重量 | float (kg) | **新增 DoorStatus.weight** |
| `fullness` | 满溢度 (0-100) | int32 | **新增 DoorStatus.fullness** |
| `spillAlarm` | 满溢标志 | bool | **DoorStatus.spillAlarm**（由设备级迁入） |
| `smokeAlarm` | 烟雾标志 | bool | **DoorStatus.smokeAlarm**（由设备级迁入） |

> 满溢/烟雾精细到投口，便于前端按投口提示「该满了」。
> 投口的**配置**（启用、分类、单价）仍归 `biz_door`；**当前垃圾袋号/去皮**仍归 `biz_clean_bag`（由 `cleanTare` 事件维护）——`doorStates` 只承载实时重量/告警**快照**，不重复存配置与去皮，避免双写。

---

## 3. 服务（Service）— 平台下发命令

平台 → 设备，带参数、需回执。对应 `OneNetClient` 要实现的下发调用。callType 取 `async`（设备执行后另发事件回执，避免同步阻塞）。

### 3.1 `openDeliveryDoor` — 开投递投口

| 方向 | 字段 | dataType | 对应后端 |
|------|------|----------|---------|
| input | `doorIndex` | int32 | 投口号 |
| input | `deliveryToken` | string | 后端生成的投递标识，设备原样在 `deliveryComplete` 带回 |
| input | `wasteType1` | int32 | 一级分类 |
| input | `wasteType2` | int32 | 二级分类 |
| input | `cosToken` | struct | COS 上传临时密钥（搭车下发，见 §3.4） |
| output | `accepted` | bool | 设备是否受理 |

对应 `DeviceCommandService.sendOpenDoor`（现为占位日志）。下发前由 `CosTokenClient.getTempCredentials` 取临时密钥、`buildPhotoKeys` 按 `deliveryToken` 算 4 个 key，一并填入 `cosToken`。

### 3.2 `openCleanDoor` — 开清运门

| 方向 | 字段 | dataType | 对应后端 |
|------|------|----------|---------|
| input | `doorIndex` | int32 | 投口号（物理控制，开哪个投口） |
| input | `cleanOrderId` | int64 | 清运订单ID（**开门即建单**，设备原样在 `cleanGross`/`cleanTare`/`photoReport` 带回） |
| input | `cosToken` | struct | COS 上传临时密钥（搭车下发，见 §3.4） |
| output | `accepted` | bool | 设备是否受理 |

对应 `DeviceCommandService.sendOpenCleanDoor(devSn, doorIndex, cleanOrderId)`。**开门即建单**：清运员小程序 `open` 时后端已握有登录 `userId` + 扫到的新空袋编号，此刻创建 `CleanOrder`（`newBagQr` 记新袋），把 `cleanOrderId` 随命令下发。设备**不再接收 `bagNo`/`userId`**。下发前由 `CosTokenClient.getTempCredentials` 取临时密钥、`buildPhotoKeys` 按 `cleanOrderId` 算 4 个 key，一并填入 `cosToken`。

### 3.3 `reboot` — 远程重启（运维预留）

无入参，output `accepted` bool。

### 3.4 `cosToken` 结构（搭车 §3.1 / §3.2 下发）

COS 临时上传密钥不另开服务，作为 struct 入参随开门命令下发，设备开门即拿到、直传 COS 时使用。
凭证字段对齐后端 `framework/cos/CosStsCredential`；4 个 key 由 `CosTokenClient.buildPhotoKeys` 按订单 token 生成。

> ⚠ **OneNet 字符串字段上限 512**，而 STS `sessionToken` 实测约 **640**（随 policy 浮动），单字段装不下，故**拆成 `sessionToken1` + `sessionToken2` 两段**（各 512，合计 1024）。后端下发时按序切分，**固件按 `sessionToken1 + sessionToken2` 顺序拼接还原**完整令牌。实测：`tmpSecretId`=68、`tmpSecretKey`=44、`sessionToken`=640、`expiredTime`=10 位 epoch。
>
> 照片 **key 由后端确定性生成并下发**（取代旧的 `uploadPrefix` + 设备自取文件名 + 回传 URL 方案）。设备把 4 张照片**分别**直传到对应 key，无需回传——后端开门时已据同一 key 预存订单 URL。key 形如 `{sn}/{doorIndex}/{token}/open_outside.jpg`。

| 子字段 | dataType | 说明 |
|--------|----------|------|
| `tmpSecretId` | string(128) | STS 临时 SecretId（实测 68） |
| `tmpSecretKey` | string(128) | STS 临时 SecretKey（实测 44） |
| `sessionToken1` | string(512) | STS 会话令牌·前段（与 2 拼接还原） |
| `sessionToken2` | string(512) | STS 会话令牌·后段 |
| `bucket` | string(128) | COS 桶名 |
| `region` | string(32) | COS 地域 |
| `baseUrl` | string(256) | COS 访问域名 |
| `keyOpenOutside` | string(128) | 开门前·箱外照片对象 key |
| `keyOpenInside` | string(128) | 开门前·箱内照片对象 key |
| `keyCloseOutside` | string(128) | 关门后·箱外照片对象 key |
| `keyCloseInside` | string(128) | 关门后·箱内照片对象 key |
| `expire` | int64 | 密钥过期时间（epoch 秒） |

---

## 4. 事件（Event）— 设备主动上报业务数据

离散、一次一条、需幂等。这是与 `/api/iot/**` 端点**直接对应**的部分，输出参数 = 现有 DTO 字段（去掉 `sn`，由报文头身份补回）。

### 4.1 `deliveryComplete`（info）— 投递完成（投递两阶段·阶段2）

| identifier | dataType | 对应 DeliveryReportRequest |
|------------|----------|---------------------------|
| `deliveryToken` | string | deliveryToken（开投口时下发，原样带回做配对） |
| `weight` | float (kg) | weight |
| `wasteType1` | int32 | wasteType1（可选） |
| `wasteType2` | int32 | wasteType2（可选） |

### 4.2 `cleanGross`（info）— 清运毛重（清运·图④）

| identifier | dataType | 对应 CleanGrossRequest |
|------------|----------|------------------------|
| `cleanOrderId` | int64 | cleanOrderId（开门时下发，原样带回；**充当幂等键**） |
| `weight` | float (kg) | weight（满袋毛重） |

> 设备只回传 `cleanOrderId` + 毛重；`doorIndex`/`userId` 由后端按订单反查。后端按 `cleanOrderId` 定位订单：已回填毛重则幂等返回，否则 `net = 毛重 − 该投口当前(旧袋)去皮`。

### 4.3 `cleanTare`（info）— 去皮上报（清运·图⑤，换新空袋）

| identifier | dataType | 对应 CleanTareRequest |
|------------|----------|------------------------|
| `cleanOrderId` | int64 | cleanOrderId（开门时下发，原样带回） |
| `weight` | float (kg) | weight（新空袋去皮重） |

> 设备**不传 bagNo**：新袋编号 open 时已由小程序扫到并记在订单 `newBagQr`。后端按 `cleanOrderId` 取订单的 `newBagQr` + 本次去皮重，upsert `biz_clean_bag (device_id, door_index)`。

### 4.4 `spillAlarm`（alert）— 满溢告警

| identifier | dataType | 说明 |
|------------|----------|------|
| `doorIndex` | int32 | 哪个投口 |
| `fullness` | int32 | 满溢度 0-100 |

### 4.5 `smokeAlarm`（error）— 烟雾/火灾告警

| identifier | dataType | 说明 |
|------------|----------|------|
| `doorIndex` | int32 | 哪个投口（整机告警可传 0） |
| `temperature` | float | 温度（可选） |

> §4.4 / §4.5 对应「设备状态/告警上报」功能缺口，待后端补告警处理端点（`open-items.md` §2）。

> **无 `photoReport` 事件**：照片 key 由后端开门时确定性生成并下发（§3.4），后端开门即按同一 key 预存订单
> 4 张照片 URL，设备直传到对应 key 即可，不再回传 URL。

---

## 5. OneNet 物模型导入 JSON

> **导入文件**：**`docs/onenet-thing-model.json`**（已按 OneNet 控制台格式校验，可直接上传）。本文不再内嵌 JSON 副本以免脱节，功能点结构以 §2/§3/§4 表格为准。
>
> OneNet 格式要点（实测踩坑）：
> - 每个功能点（属性/服务/事件）必须带 `functionType`（自定义填 `"u"`）；事件输出字段用 `outputData`（非 `output`）。
> - `bool` 的 specs 用 `{"true":..,"false":..}`；枚举用 `enum`；`array` 用 `{"length":N,"type":..,"specs":..}`。
> - `string` 的 `length` 为**无引号整数、范围 1-512**；超长字段（如 STS `sessionToken`≈640）须拆成多段（见 §3.4）。
> - `name` 限 1-32 位、仅中文/英文/数字/`_-`、首字符为中英文（不可含括号等符号）。`identifier` 用英文驼峰。

---

## 6. 与后端对接的字段映射速查

| 物模型功能点 | 类型 | 后端落点 | 状态 |
|--------------|------|---------|------|
| `openDeliveryDoor` | service | `DeviceCommandService.sendOpenDoor` → `OneNetClient.openDeliveryDoor`(invokeService) | 真实下发已实现（门控 isConfigured），本地未测待凭证联调 |
| `openCleanDoor`(含 cleanOrderId) | service | `CleanOrderService.openCleanDoor`（**开门即建单**）→ `DeviceCommandService.sendOpenCleanDoor` → `OneNetClient.openCleanDoor` | 建单已实现；OneNet 真实下发已实现（门控），本地未测待凭证联调 |
| `cosToken`(搭车 openDoor) | service 入参 | `CosTokenClient.getTempCredentials`(凭证) + `buildPhotoKeys`(4 个 key) → `OneNetClient` 按 512 拆 `sessionToken1/2` 填入下发 | 已实现（搭车 openDoor），待凭证联调 |
| 照片 URL（开门即存） | — | 开门时 `CosTokenClient.buildPhotoKeys`+`toUrl` 算 4 URL，直接写订单 `photo_*`（取代设备回传） | 已实现 |
| `deliveryComplete` | event | 投递两阶段 Service（`DeliveryReportRequest`） | 已实现（直连 + OneNet MQ 上行），待联调 |
| `cleanGross` | event | `CleanOrderService.reportGross`（按 `cleanOrderId` 回填，幂等） | 已实现（直连 + OneNet MQ 上行），待联调 |
| `cleanTare` | event | `CleanOrderService.reportTare`（按 `cleanOrderId` 取新袋去皮） | 已实现（直连 + OneNet MQ 上行），待联调 |
| `online/voltage/rssi/fwVersion` | property | `DeviceStatusService`（`biz_device_status`，去 totalWeight/spill/smoke） | 待补 Service/Controller |
| `doorStates`(weight/fullness/spill/smoke) | property | `DoorStatusService`（`biz_door_status`，**新表**） | 待建表 + Service/Controller |
| `spillAlarm/smokeAlarm` | event | 告警处理端点（同时刷新 `biz_door_status` 标志位） | 待建 |

---

## 7. 待确认 / 待办

1. **库表调整（需新增迁移 V10）**：`biz_device_status` 去 `total_weight`/`spill_alarm`/`smoke_alarm`、加 `rssi`/`fw_version`；新建 `biz_door_status`（投口级重量/满溢/烟雾快照，`UNIQUE(device_id, door_index)`）。同步改 `DeviceStatus` 实体、新增 `DoorStatus` 实体与 H2 测试 schema。详见 `database-design.md` §8/§8b。
2. **OneNet → 后端通道（北向）**：✅ **已实现并联调通过上行**（2026-06-10，控制台模拟设备 test-divice-1）。后端作 **Pulsar 消费者**拨出去连 OneNet 北向 MQ（`pulsar+ssl://iot-north-mq.heclouds.com:6651/`，topic `{accessId}/iot/event`），免公网、可靠不丢、削峰。实现：`framework/onenet/OneNetMqConsumer`（自定义鉴权 `OneNetAuthentication`）→ 解第一层报文 → `OneNetCipher` 解密 → `business/onenet/OneNetEventDispatcher` 按 `msgType`/事件 identifier 分发到现有 Service。消费组凭证由 `.env` 注入（`iotAccessId/iotSecretKey/iotSubscriptionName`）。
   - **报文结构（已据官方「服务端订阅消息类型」文档确认）**：两层。第一层 `{ superMsg, pv, t, data, sign }`，`data` 为 **AES 加密 Base64**（算法 `AES/ECB/PKCS5`，key = 消费组 KEY 的 `substring(8,24)`）；解密后第二层 `{ "msgType": <类型>, "subData": { deviceName, productId, deviceId, imei, params/... } }`。
   - **身份**在 `subData.deviceName`（= `biz_device.sn`），印证 §0「身份不进 payload」。**业务事件**走 `msgType=thingEvent`，输出在 `subData.params` 按 identifier 承载；属性 `thingProperty`、上下线 `deviceOnline/Offline`、下发回执 `thingServiceReply`。
   - **事件输出结构（已用真实报文确认）**：`thingEvent` 单个事件**被 `value` 包裹**，形如 `params.cleanGross = {"time":<ms>,"value":{"weight":10,"cleanOrderId":45}}`；`OneNetEventDispatcher.unwrap` 正确解出。真实报文不含 `imei` 字段（无妨，按需取）。
   - **联调实测（2026-06-10）**：控制台设备模拟器以 test-divice-1 发 cleanGross → consumer 收到并解密 → 分发器调 `reportGross` 成功（仅因测试单不存在而抛"订单不存在"，属预期）。整条上行（连接/解密/解析/分发）零问题。
   - HTTP 推送（`POST /api/iot/onenet/notify`）仍仅作有公网 IP 时的备选，未实现。
2b. **图片链路（后端定 key + 开门即存 URL，已落地）**：
   - 后端开门时调 `CosTokenClient.getTempCredentials`(凭证) + `buildPhotoKeys`(按 token 算 4 个 key) 塞进 `cosToken` 随开门下发（凭证待配）；同时用 `toUrl` 把 4 个完整 URL 直接写进订单 `photo_*`（投递在 insert 前、清运在 save 后 updateById）。
   - **照片 key 约定**：`{sn}/{doorIndex}/{token}/<slot>.jpg`，token = 投递 `deliveryToken` / 清运 `cleanOrderId`，slot ∈ `open_outside`/`open_inside`/`close_outside`/`close_inside`。设备把 4 张照片**分别**直传到 `cosToken` 里对应 key，**不再回传 URL**（`photoReport` 事件、`/api/iot/photo/**` 端点、`PhotoNotifyService` 均已移除）。
   - **固件约定（sessionToken 拼接）**：STS 令牌实测约 640 > OneNet 512 上限，已拆 `sessionToken1`+`sessionToken2`。后端下发时按序切分（前 512 + 余下）；**固件须按 `sessionToken1 + sessionToken2` 顺序拼接还原**完整令牌再用于 COS 直传。
   - **兜底**：后端不校验对象是否真上传成功；设备没传上时前端加载出 404 显示占位图。
3. **清运 `userId` 来源**：已定为**小程序扫码登录态**——`openCleanDoor` 建单时由后端 `SecurityUtils` 取登录清运员写入订单，设备不再上报 `userId`（现仅支持小程序扫码登录）。
4. **`cleanOrderId` 幂等**：清运毛重以 `cleanOrderId` 为幂等键（一单一次毛重，重复上报不覆盖），取代原设备生成的 `reportSn`。设备只需原样回传开门下发的 `cleanOrderId`。
5. **下发 API 规格**：OneNet 服务调用（同步/异步命令下发）的具体 HTTP API、鉴权（token 签名算法）待凭证与文档到位后补 `OneNetClient` 实现，并填 `application.yml` 的 `onenet` 段。
6. **物模型 schema 校验**：§5 JSON 按 OneNet 标准物模型常见格式编写，导入前需在 OneNet 控制台实测，按平台实际 schema 版本微调。

