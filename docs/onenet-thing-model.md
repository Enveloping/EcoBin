# EcoBin OneNet 物模型设计

> 面向中国移动 OneNet 平台的设备物模型定义，供 OneNet 控制台导入与设备固件、后端对接共同遵循。
> 本文是「物模型字段口径」的单一来源：物模型功能点标识符与后端 `/api/iot/**` DTO 字段一一对齐，避免数据转发时再做字段映射。
> 初版：2026-06-09。**物模型结构以 `docs/onenet-thing-model.json` 为单一来源**（已导入 OneNet 控制台）；本文表格仅用于「功能点 ↔ 后端字段」口径对齐，dataType/specs 细节一律以 .json 为准。
> 下行真实接入已打通（2026-06-13）：凭证（product_id / access_key）已配 `.env`，服务调用 API 与鉴权已据官方文档确认（见 §3.5 / `docs/references/设备服务调用.md`、`安全鉴权.md`）。

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
                          后端 MQ 消费者（OneNetEventDispatcher）   ◄── OneNet 报文适配入口
                                     │ 解包 msgType + params，按事件标识符分发
                                     ├─► deliveryComplete ─► DeliveryOrderService.completeDelivery（上传后建单）
                                     ├─► cleanGross ───────► CleanOrderService.reportGross
                                     ├─► cleanTare ────────► CleanOrderService.reportTare
                                     └─► 属性/告警 ────────► DeviceStatusService（待补）

后端下发：DeviceCommandService → OneNetClient → OneNet 服务调用 API → 设备
          ├─ openDeliveryDoor   （投递开投口，sendOpenDoor；cosToken 仅凭证）
          └─ openCleanDoor      （清运开门，sendOpenCleanDoor；cosToken 含凭证 + 4 个 key）
```

**关键决策**：
- 北向（设备→后端）**以消息队列 MQ 订阅为主**：后端作消费者主动连 OneNet，免公网、可靠不丢、天然削峰（设备多时优势明显）。HTTP 推送仅作「有公网 IP 时」的备选；二者解包后复用同一批 Service，互不影响。
- 保留现有 `/api/iot/clean/gross`、`/api/iot/clean/tare`、`/api/iot/delivery/complete` 端点给「设备直连联调」用（照片 `/api/iot/photo/**` 已移除）。

### 1.1 图片链路（抓拍 4 张：开门前/关门后 × 箱内/箱外）

图片二进制由**设备直传腾讯云 COS**（设备↔COS 公网直连，不经后端、不占后端带宽）。URL 归属两端不同：**清运由后端定 key、不回传；投递由设备定位置、随 `deliveryComplete` 回传 4 个 URL**（见下）。
照片对象 key 两端用同一约定格式 `{sn}/{doorIndex}/{token}/<slot>.jpg`，但 **token 的产生方与建单时机按业务不同**：

```
投递（上传后建单，见 §8）：
  ① 后端 openDeliveryDoor 只下发凭证（cosToken 不含 key）
  ② 设备自定照片对象 key（位置由设备决定，建议带唯一串避免覆盖）→ 直传 COS
  ③ 设备 deliveryComplete 带回 doorIndex + **4 个照片 URL** → 后端建单、原样存 URL（不复原）

清运（开门即建单）：
  ① 后端 open 即建单（已知 cleanOrderId），按 cleanOrderId 生成 4 个 key、预存订单 4 个 URL
  ② 后端 openCleanDoor 只下发凭证（不含 key）；设备据下发的 cleanOrderId 按约定自拼同样的 key 直传（完）
```

> 清运照片 key 用 `cleanOrderId`（后端定、设备按约定自拼，两端必须一致）；投递照片位置设备自定、URL 回传，与订单一一对应避免覆盖。
> **兜底**：后端不校验对象是否真上传成功；设备若没传上，前端加载出 404 显示占位图。

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
| input | `wasteType1` | int32 | 一级分类 |
| input | `wasteType2` | int32 | 二级分类 |
| input | `cosToken` | struct | COS 上传临时密钥（**仅凭证**，搭车下发，见 §3.4） |
| output | `accepted` | bool | 设备是否受理 |

对应 `DeviceCommandService.sendOpenDoor(devSn, doorIndex)`。**投递为「上传后建单」**（见 §8）：照片位置由**设备**决定（含设备屏「继续投递」本地再开门，设备自定对象 key 直传、URL 随上报回传），故开门命令<strong>不下发照片 key</strong>，`cosToken` 只含凭证。下发前由 `CosTokenClient.getTempCredentials` 取临时密钥填入 `cosToken`，设备可整会话缓存复用。

> ⚠ **所有 input 均为必填**：OneNet 服务调用按物模型校验入参，缺值/传 `null` 会报 `10415 设备服务调用失败:required value`。小程序开投递门未指定分类时，`OneNetClient.openDeliveryDoor` 把 `wasteType1`/`wasteType2` 兜底为 `0`（= 缺省/不区分，设备侧仍按投口配置兜底），避免缺值。

### 3.2 `openCleanDoor` — 开清运门

| 方向 | 字段 | dataType | 对应后端 |
|------|------|----------|---------|
| input | `doorIndex` | int32 | 投口号（物理控制，开哪个投口） |
| input | `cleanOrderId` | int64 | 清运订单ID（**开门即建单**，设备原样在 `cleanGross`/`cleanTare` 带回） |
| input | `cosToken` | struct | COS 上传临时密钥（搭车下发，见 §3.4） |
| output | `accepted` | bool | 设备是否受理 |

对应 `DeviceCommandService.sendOpenCleanDoor(devSn, doorIndex, cleanOrderId)`。**开门即建单**：清运员小程序 `open` 时后端已握有登录 `userId` + 扫到的新空袋编号，此刻创建 `CleanOrder`（`newBagQr` 记新袋），把 `cleanOrderId` 随命令下发。设备**不再接收 `bagNo`/`userId`**。下发前由 `CosTokenClient.getTempCredentials` 取临时密钥、`buildPhotoKeys` 按 `cleanOrderId` 算 4 个 key，一并填入 `cosToken`。

### 3.3 `reboot` — 远程重启（运维预留）

无入参，output `accepted` bool。

### 3.4 `cosToken` 结构（搭车 §3.1 / §3.2 下发）

COS 临时上传密钥不另开服务，作为 struct 入参随开门命令下发，设备开门即拿到、直传 COS 时使用。
凭证字段对齐后端 `framework/cos/CosStsCredential`。**cosToken 只含凭证，不含照片 key、不含 expire**，由 `OneNetClient.baseCosToken` 组装（投递/清运通用）。

> ⚠ **OneNet 字符串字段上限 512**，而 STS `sessionToken` 实测约 **640**（随 policy 浮动），单字段装不下，故**拆成 `sessionToken1` + `sessionToken2` 两段**（各 512，合计 1024）。后端下发时按序切分，**固件按 `sessionToken1 + sessionToken2` 顺序拼接还原**完整令牌。实测：`tmpSecretId`=68、`tmpSecretKey`=44、`sessionToken`=640。
>
> **照片 key 不随命令下发**，两服务都只发凭证；设备各自决定上传位置：
> - **`openDeliveryDoor`（投递）**：「上传后建单 + 设备本地继续投递」，开门时后端没有订单标识、继续投递的本地再开门后端也够不着，故**照片位置由设备决定**（设备自定对象 key 直传 COS），并在 `deliveryComplete` 里**随称重一并回传 4 个照片 URL**，后端原样存（不复原）。
> - **`openCleanDoor`（清运）**：「开门即建单」，开门时已知 `cleanOrderId` 并随命令下发；设备据其按 `{sn}/{doorIndex}/{cleanOrderId}/<slot>.jpg` **自拼 key** 直传，后端开门即按同一公式预存订单 URL、**不回传**。
>
> 槽位语义两端一致（slot ∈ `open_outside`/`open_inside`/`close_outside`/`close_inside`）。

| 子字段 | dataType | 说明 |
|--------|----------|------|
| `tmpSecretId` | string(128) | STS 临时 SecretId（实测 68） |
| `tmpSecretKey` | string(128) | STS 临时 SecretKey（实测 44） |
| `sessionToken1` | string(512) | STS 会话令牌·前段（与 2 拼接还原） |
| `sessionToken2` | string(512) | STS 会话令牌·后段 |
| `bucket` | string(128) | COS 桶名 |
| `region` | string(32) | COS 地域 |
| `baseUrl` | string(256) | COS 访问域名 |

> **`cosToken` 仅含凭证**：两服务（openDeliveryDoor / openCleanDoor）的 cosToken 结构一致，**不含照片 key、不含 expire**。
> - 照片 key：投递由设备自定位置；清运由设备据下发的 `cleanOrderId` 按 `{sn}/{doorIndex}/{cleanOrderId}/<slot>.jpg` 自拼（后端开门即按同一公式预存 URL）。
> - expire：去除，设备用凭证直传，失效即重新开门取新凭证。

### 3.5 下发 API 与鉴权（AIoT 融合平台，已据官方文档确认 2026-06-13）

`OneNetClient.invokeService` 调「设备服务调用」API，凭证 `productId`/`accessKey` 经 `.env`（`onenetProductId`/`onenetAccessKey`）注入；缺失时 `isConfigured()` 为假、走占位日志不真发。

| 项 | 值 | 备注 |
|----|----|------|
| 接口地址 | `POST https://iot-api.heclouds.com/thingmodel/call-service` | host 在 `application.yml` `onenet.base-url`，path 在 `OneNetProperties.invokeServicePath`。**注意是 `call-service` 不是 `invoke-thing-service`**（后者会 404） |
| 请求体 | `{ product_id, device_name(=sn), identifier, params }` | `params` 即各服务 input（openDeliveryDoor/openCleanDoor）；**不需要 `project_id`** |
| 成功判定 | 返回体 `code == 0`（`{code,msg,request_id,data}`） | HTTP 200 不代表成功；`code=10415` = 入参缺值；设备离线另有错误码 |
| 鉴权头 | `authorization: version=2022-05-01&res=products/{productId}&et=..&method=sha256&sign=..` | `OneNetTokenGenerator` 生成；`sign=base64(HmacSHA256(base64decode(accessKey), et\nmethod\nres\nversion))`，value 段 URL 编码 |
| accessKey 来源 | **产品级 access_key**（因 `res=products/{productId}`） | 控制台「产品开发→产品信息页」取，非主账号/项目 key |

> 依赖：`callType:async`，平台 `code=0` 仅表示**受理并转发**，命令是否到设备看设备是否在线（文档明确"仅支持 OneJson 设备，需要设备在线"）。`docs/references/设备服务调用.md`、`安全鉴权.md` 为官方原文留档。

---

## 4. 事件（Event）— 设备主动上报业务数据

离散、一次一条、需幂等。这是与 `/api/iot/**` 端点**直接对应**的部分，输出参数 = 现有 DTO 字段（去掉 `sn`，由报文头身份补回）。

### 4.1 `deliveryComplete`（info）— 投递完成（投递·上传后建单，见 §8）

| identifier | dataType | 对应 DeliveryReportRequest |
|------------|----------|---------------------------|
| `doorIndex` | int32 | doorIndex（设备开的哪个投口；后端据 device+doorIndex 反查投口取单价/分类） |
| `weight` | float (kg) | weight |
| `wasteType1` | int32 | wasteType1（可选） |
| `wasteType2` | int32 | wasteType2（可选） |
| `photoOpenOutside` | string | photoOpenOutside（可选，开门前·箱外照片 URL，**设备回传**） |
| `photoOpenInside` | string | photoOpenInside（可选，开门前·箱内照片 URL，**设备回传**） |
| `photoCloseOutside` | string | photoCloseOutside（可选，关门后·箱外照片 URL，**设备回传**） |
| `photoCloseInside` | string | photoCloseInside（可选，关门后·箱内照片 URL，**设备回传**） |

> 后端收到本事件时才建单（不预建）：归属取该设备「当前活跃用户」会话，无活跃会话则建无主单、不返现（见 §8）。
> **幂等**：已去设备生成的 `deliveryToken`，改按 **OneNet 消息 id**（分发器取报文 `id`，缺失回退 MQ 传输层 messageId）作幂等键，落 `biz_delivery_order.delivery_token`（唯一索引），适配 MQ at-least-once 重投。直连上报未带消息 id 则不去重。
> **照片随本次称重一并回传**：投递「上传后建单 + 设备本地继续投递」，开门时后端尚无订单标识、继续投递的本地再开门后端也够不着，故**照片位置由设备决定并随上报回传 4 个 URL，后端原样存**（不再按公式复原）。缺失的槽位前端显示占位图。清运不同（见 §3.2 / §3.4，开门即建单、后端定 key、不回传）。

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

> **无独立 `photoReport` 事件**：
> - **清运**：照片 key 由后端开门时确定性生成并下发（§3.4），后端开门即预存订单 4 张 URL，设备直传到对应 key 即可，**不回传**。
> - **投递**：照片位置由设备决定，4 个 URL **随 `deliveryComplete` 一并回传**（见 §4.1），后端原样存。

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
| `openDeliveryDoor` | service | `DeliveryOrderService.openDoor`（**开启设备=建会话不建单**）→ `DeviceCommandService.sendOpenDoor(sn,doorIndex)` → `OneNetClient.openDeliveryDoor` | 真实下发已接通（凭证已配、API `/thingmodel/call-service` 已确认、wasteType 兜底 0）；平台受理待设备在线最终确认 |
| `openCleanDoor`(含 cleanOrderId) | service | `CleanOrderService.openCleanDoor`（**开门即建单**）→ `DeviceCommandService.sendOpenCleanDoor` → `OneNetClient.openCleanDoor` | 建单已实现；真实下发同上（同一 `call-service` 通道），待设备在线确认 |
| `cosToken`(仅凭证，投递/清运通用) | service 入参 | `OneNetClient.baseCosToken`（凭证，无 key、无 expire；按 512 拆 `sessionToken1/2`） | 已实现，待凭证联调 |
| 照片 URL | — | 清运开门即 `buildPhotoKeys`+`toUrl` 写订单（key 不下发，设备据 cleanOrderId 自拼）；投递由设备随 `deliveryComplete` 回传 4 个 URL，后端原样存 | 已实现 |
| 设备活跃会话 | — | `DeviceSessionService.activate/findActive/refresh`（`biz_device_session`，V13） | 已实现（开启设备建会话、上传后建单取归属） |
| `deliveryComplete`(含 doorIndex + 4 照片 URL) | event | `DeliveryOrderService.completeDelivery`（**上传后建单**，取活跃会话归属；按 OneNet 消息 id 幂等） | 已实现（直连 + OneNet MQ 上行），待联调 |
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
2b. **图片链路（设备直传 COS，已落地）**：
   - **清运（开门即建单）**：后端开门时 `getTempCredentials`(凭证) 塞进 `cosToken` 随开门下发（**不含 key**），并 `buildPhotoKeys`(按 `cleanOrderId`)+`toUrl` 把 4 个 URL 预存订单 `photo_*`；设备据下发的 `cleanOrderId` 按约定自拼同样的 key 直传，**不回传**。
   - **投递（上传后建单 + 继续投递，见 §8）**：开门只下发凭证（`baseCosToken`）；照片对象 key 由设备自定；照片 4 个 URL 由设备**随 `deliveryComplete` 回传**，后端 `completeDelivery` 原样写订单 `photo_*`（不复原）。
   - **照片 key 约定**：清运后端按 `{sn}/{doorIndex}/{cleanOrderId}/<slot>.jpg` 确定性生成；投递对象 key 由设备自定（后端不约束格式，只存设备回传的 URL）。slot 槽位语义 ∈ `open_outside`/`open_inside`/`close_outside`/`close_inside`。（无 `photoReport` 事件、`/api/iot/photo/**` 端点、`PhotoNotifyService`，均已移除。）
   - **固件约定（sessionToken 拼接）**：STS 令牌实测约 640 > OneNet 512 上限，已拆 `sessionToken1`+`sessionToken2`。后端下发时按序切分（前 512 + 余下）；**固件须按 `sessionToken1 + sessionToken2` 顺序拼接还原**完整令牌再用于 COS 直传。
   - **兜底**：后端不校验对象是否真上传成功；设备没传上时前端加载出 404 显示占位图。
3. **清运 `userId` 来源**：已定为**小程序扫码登录态**——`openCleanDoor` 建单时由后端 `SecurityUtils` 取登录清运员写入订单，设备不再上报 `userId`（现仅支持小程序扫码登录）。
4. **`cleanOrderId` 幂等**：清运毛重以 `cleanOrderId` 为幂等键（一单一次毛重，重复上报不覆盖），取代原设备生成的 `reportSn`。设备只需原样回传开门下发的 `cleanOrderId`。
5. ~~**下发 API 规格**~~ ✅ **已确认并接通（2026-06-13）**：AIoT 融合平台「设备服务调用」`POST https://iot-api.heclouds.com/thingmodel/call-service`，token `res=products/{productId}`+sha256，详见 §3.5。凭证已填 `.env`。剩：平台 `code=0` 受理后，命令到设备需设备在线，端到端待真实设备/模拟器确认。
6. ~~**物模型 schema 校验**~~ ✅ 已导入 OneNet 控制台（`docs/onenet-thing-model.json` 为单一来源）。

---

## 8. 投递：上传后建单 + 设备会话关联用户（支持设备端「继续投递」）

为支持设备屏「继续投递」（用户投完一袋后在设备端直接再开门投下一袋，不必回小程序），投递从
「后端开门即建单」改为「**设备上传称重后后端才建单**」，用户身份靠后端维护的「设备当前活跃用户」会话关联。
清运不受影响（仍开门即建单）。

**流程**：
1. **开启设备**（小程序·登录态，`POST /api/app/delivery/open`）：后端取登录 `userId`，按 `device_id` upsert
   `biz_device_session`（当前活跃用户，TTL 默认 15 分钟），下发 `openDeliveryDoor`（仅凭证）。**不建单**。
2. **设备作业 + 继续投递**：设备开门（首次后端触发 / 续投设备本地开），
   投放→称重→设备自定照片对象 key 直传 COS（复用缓存凭证），记下 4 个 URL 待上报回传。
3. **上传后建单**：设备 `deliveryComplete{doorIndex, weight, photo*..., ...}` 上行（MQ / 直连）。后端：
   - 按 `sn` 反查设备；按 **device + OneNet 消息 id** 幂等去重（落 `delivery_token` 列；已建单则忽略，见 §4.1）；
   - 按 `device+doorIndex` 反查投口（取单价/分类兜底）；
   - 4 个照片 URL 取上报回传值原样写订单（缺失则前端占位）；
   - 查该设备 `findActive` 活跃会话 → 命中则建单归该用户、`deliveryStatus=1`、按单价×重量返现入账、续期会话；
   - **未命中**（会话过期 / 从未开启）→ 建**无主单**（`user_id=null`、归设备租户、不返现、`log.warn` 告警）。

**会话覆盖语义**：`biz_device_session` 按 `device_id` 唯一，后来用户开启会覆盖上一行（接受「最近用户」语义）；
同一台箱子两人交叠极罕见，交叠时延迟上传会归最新用户，不额外处理。

**代码落点**：`DeviceSessionService`（device 模块）、`DeliveryOrderServiceImpl.openDoor/completeDelivery`、
`DeliveryReportRequest`（+`doorIndex`）、`OneNetClient.openDeliveryDoor`（仅凭证）、迁移 `V13__add_device_session.sql`。

