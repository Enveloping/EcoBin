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
                                     │ 数据转发 / HTTP 推送（OneNet 包装报文）
                                     ▼
                          后端 POST /api/iot/onenet/notify   ◄── 待建：OneNet 报文适配入口
                                     │ 解包 notify_type + payload，按事件标识符分发
                                     ├─► deliveryComplete ─► 现有投递两阶段 Service
                                     ├─► cleanGross ───────► CleanOrderService.reportGross
                                     ├─► cleanTare ────────► CleanOrderService.reportTare
                                     └─► 属性/告警 ────────► DeviceStatusService（待补）

后端下发：DeviceCommandService → OneNetClient → OneNet 服务调用 API → 设备
          ├─ openDeliveryDoor   （投递开投口，对应 sendOpenDoor）
          └─ openCleanDoor      （清运开门，对应 sendOpenCleanDoor）
```

**关键决策**：保留现有 `/api/iot/clean/gross`、`/api/iot/clean/tare`、投递上报端点给「设备直连联调」用；OneNet 生产链路另开 `POST /api/iot/onenet/notify` 统一入口，解包后复用同一批 Service，互不影响。

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
| output | `accepted` | bool | 设备是否受理 |

对应 `DeviceCommandService.sendOpenDoor`（现为占位日志）。

### 3.2 `openCleanDoor` — 开清运门

| 方向 | 字段 | dataType | 对应后端 |
|------|------|----------|---------|
| input | `doorIndex` | int32 | 投口号 |
| input | `bagNo` | string | 本次清运换上的新空袋编号 |
| output | `accepted` | bool | 设备是否受理 |

对应 `DeviceCommandService.sendOpenCleanDoor(devSn, doorIndex, bagNo)`。

### 3.3 `reboot` — 远程重启（运维预留）

无入参，output `accepted` bool。

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
| `doorIndex` | int32 | doorIndex |
| `userId` | int64 | userId（设备当前登录清运员） |
| `weight` | float (kg) | weight（满袋毛重） |
| `reportSn` | string | reportSn（**幂等键，设备生成**，重传不变） |

> ⚠ `reportSn` 必须由设备生成并固定，**不能**用 OneNet 自动消息 ID（每次重传都会变，破坏幂等）。后端按 `reportSn` 去重，`net = 毛重 − 该投口当前去皮`。

### 4.3 `cleanTare`（info）— 去皮上报（清运·图⑤，换新空袋）

| identifier | dataType | 对应 CleanTareRequest |
|------------|----------|------------------------|
| `doorIndex` | int32 | doorIndex |
| `userId` | int64 | userId |
| `bagNo` | string | bagNo（新垃圾袋编号） |
| `weight` | float (kg) | weight（新空袋去皮重） |

> 后端 upsert `biz_clean_bag (device_id, door_index)`：写入新 `bagNo` + `tareWeight`。

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

---

## 5. OneNet 物模型导入 JSON

> 可直接导入 OneNet 控制台「物模型」。如平台 schema 版本差异，以本文表格为准微调字段。

```json
{
  "properties": [
    {
      "identifier": "online",
      "name": "在线状态",
      "accessMode": "r",
      "required": false,
      "dataType": { "type": "bool", "specs": { "0": "离线", "1": "在线" } }
    },
    {
      "identifier": "voltage",
      "name": "电压",
      "accessMode": "r",
      "required": false,
      "dataType": { "type": "float", "specs": { "min": "0", "max": "60", "unit": "V", "step": "0.1" } }
    },
    {
      "identifier": "rssi",
      "name": "信号强度",
      "accessMode": "r",
      "required": false,
      "dataType": { "type": "int32", "specs": { "min": "-150", "max": "0", "unit": "dBm", "step": "1" } }
    },
    {
      "identifier": "fwVersion",
      "name": "固件版本",
      "accessMode": "r",
      "required": false,
      "dataType": { "type": "string", "specs": { "length": "32" } }
    },
    {
      "identifier": "doorStates",
      "name": "各投口状态",
      "accessMode": "r",
      "required": false,
      "dataType": {
        "type": "array",
        "specs": {
          "size": "6",
          "item": {
            "type": "struct",
            "specs": [
              { "identifier": "doorIndex", "name": "投口号", "dataType": { "type": "int32", "specs": { "min": "1", "max": "6", "step": "1" } } },
              { "identifier": "weight", "name": "当前即时重量", "dataType": { "type": "float", "specs": { "min": "0", "max": "1000", "unit": "kg", "step": "0.001" } } },
              { "identifier": "fullness", "name": "满溢度", "dataType": { "type": "int32", "specs": { "min": "0", "max": "100", "unit": "%", "step": "1" } } },
              { "identifier": "spillAlarm", "name": "满溢标志", "dataType": { "type": "bool", "specs": { "0": "正常", "1": "满溢" } } },
              { "identifier": "smokeAlarm", "name": "烟雾标志", "dataType": { "type": "bool", "specs": { "0": "正常", "1": "报警" } } }
            ]
          }
        }
      }
    }
  ],
  "services": [
    {
      "identifier": "openDeliveryDoor",
      "name": "开投递投口",
      "callType": "async",
      "required": false,
      "input": [
        { "identifier": "doorIndex", "name": "投口号", "dataType": { "type": "int32", "specs": { "min": "1", "max": "6", "step": "1" } } },
        { "identifier": "deliveryToken", "name": "投递标识", "dataType": { "type": "string", "specs": { "length": "64" } } },
        { "identifier": "wasteType1", "name": "一级分类", "dataType": { "type": "int32", "specs": { "min": "0", "max": "99", "step": "1" } } },
        { "identifier": "wasteType2", "name": "二级分类", "dataType": { "type": "int32", "specs": { "min": "0", "max": "99", "step": "1" } } }
      ],
      "output": [
        { "identifier": "accepted", "name": "是否受理", "dataType": { "type": "bool", "specs": { "0": "拒绝", "1": "受理" } } }
      ]
    },
    {
      "identifier": "openCleanDoor",
      "name": "开清运门",
      "callType": "async",
      "required": false,
      "input": [
        { "identifier": "doorIndex", "name": "投口号", "dataType": { "type": "int32", "specs": { "min": "1", "max": "6", "step": "1" } } },
        { "identifier": "bagNo", "name": "新垃圾袋号", "dataType": { "type": "string", "specs": { "length": "64" } } }
      ],
      "output": [
        { "identifier": "accepted", "name": "是否受理", "dataType": { "type": "bool", "specs": { "0": "拒绝", "1": "受理" } } }
      ]
    },
    {
      "identifier": "reboot",
      "name": "远程重启",
      "callType": "async",
      "required": false,
      "input": [],
      "output": [
        { "identifier": "accepted", "name": "是否受理", "dataType": { "type": "bool", "specs": { "0": "拒绝", "1": "受理" } } }
      ]
    }
  ],
  "events": [
    {
      "identifier": "deliveryComplete",
      "name": "投递完成",
      "eventType": "info",
      "required": false,
      "output": [
        { "identifier": "deliveryToken", "name": "投递标识", "dataType": { "type": "string", "specs": { "length": "64" } } },
        { "identifier": "weight", "name": "投递重量", "dataType": { "type": "float", "specs": { "min": "0", "max": "1000", "unit": "kg", "step": "0.001" } } },
        { "identifier": "wasteType1", "name": "一级分类", "dataType": { "type": "int32", "specs": { "min": "0", "max": "99", "step": "1" } } },
        { "identifier": "wasteType2", "name": "二级分类", "dataType": { "type": "int32", "specs": { "min": "0", "max": "99", "step": "1" } } }
      ]
    },
    {
      "identifier": "cleanGross",
      "name": "清运毛重",
      "eventType": "info",
      "required": false,
      "output": [
        { "identifier": "doorIndex", "name": "投口号", "dataType": { "type": "int32", "specs": { "min": "1", "max": "6", "step": "1" } } },
        { "identifier": "userId", "name": "清运人ID", "dataType": { "type": "int64", "specs": { "min": "0", "max": "9999999999", "step": "1" } } },
        { "identifier": "weight", "name": "清运毛重", "dataType": { "type": "float", "specs": { "min": "0", "max": "1000", "unit": "kg", "step": "0.001" } } },
        { "identifier": "reportSn", "name": "上报订单号(幂等键)", "dataType": { "type": "string", "specs": { "length": "64" } } }
      ]
    },
    {
      "identifier": "cleanTare",
      "name": "去皮上报",
      "eventType": "info",
      "required": false,
      "output": [
        { "identifier": "doorIndex", "name": "投口号", "dataType": { "type": "int32", "specs": { "min": "1", "max": "6", "step": "1" } } },
        { "identifier": "userId", "name": "清运人ID", "dataType": { "type": "int64", "specs": { "min": "0", "max": "9999999999", "step": "1" } } },
        { "identifier": "bagNo", "name": "新垃圾袋号", "dataType": { "type": "string", "specs": { "length": "64" } } },
        { "identifier": "weight", "name": "去皮重量", "dataType": { "type": "float", "specs": { "min": "0", "max": "100", "unit": "kg", "step": "0.001" } } }
      ]
    },
    {
      "identifier": "spillAlarm",
      "name": "满溢告警",
      "eventType": "alert",
      "required": false,
      "output": [
        { "identifier": "doorIndex", "name": "投口号", "dataType": { "type": "int32", "specs": { "min": "0", "max": "6", "step": "1" } } },
        { "identifier": "fullness", "name": "满溢度", "dataType": { "type": "int32", "specs": { "min": "0", "max": "100", "unit": "%", "step": "1" } } }
      ]
    },
    {
      "identifier": "smokeAlarm",
      "name": "烟雾告警",
      "eventType": "error",
      "required": false,
      "output": [
        { "identifier": "doorIndex", "name": "投口号", "dataType": { "type": "int32", "specs": { "min": "0", "max": "6", "step": "1" } } },
        { "identifier": "temperature", "name": "温度", "dataType": { "type": "float", "specs": { "min": "-40", "max": "300", "unit": "℃", "step": "0.1" } } }
      ]
    }
  ]
}
```

---

## 6. 与后端对接的字段映射速查

| 物模型功能点 | 类型 | 后端落点 | 状态 |
|--------------|------|---------|------|
| `openDeliveryDoor` | service | `DeviceCommandService.sendOpenDoor` → `OneNetClient` | 占位，待凭证 |
| `openCleanDoor` | service | `DeviceCommandService.sendOpenCleanDoor` → `OneNetClient` | 占位，待凭证 |
| `deliveryComplete` | event | 投递两阶段 Service（`DeliveryReportRequest`） | 已实现（直连），待 OneNet notify 适配 |
| `cleanGross` | event | `CleanOrderService.reportGross`（`CleanGrossRequest`） | 已实现（直连），待 OneNet notify 适配 |
| `cleanTare` | event | `CleanOrderService.reportTare`（`CleanTareRequest`） | 已实现（直连），待 OneNet notify 适配 |
| `online/voltage/rssi/fwVersion` | property | `DeviceStatusService`（`biz_device_status`，去 totalWeight/spill/smoke） | 待补 Service/Controller |
| `doorStates`(weight/fullness/spill/smoke) | property | `DoorStatusService`（`biz_door_status`，**新表**） | 待建表 + Service/Controller |
| `spillAlarm/smokeAlarm` | event | 告警处理端点（同时刷新 `biz_door_status` 标志位） | 待建 |

---

## 7. 待确认 / 待办

1. **库表调整（需新增迁移 V10）**：`biz_device_status` 去 `total_weight`/`spill_alarm`/`smoke_alarm`、加 `rssi`/`fw_version`；新建 `biz_door_status`（投口级重量/满溢/烟雾快照，`UNIQUE(device_id, door_index)`）。同步改 `DeviceStatus` 实体、新增 `DoorStatus` 实体与 H2 测试 schema。详见 `database-design.md` §8/§8b。
2. **OneNet → 后端通道**：需建 `POST /api/iot/onenet/notify` 适配入口，解 OneNet 数据转发的包装报文（外层 `device_name`/`notify_type`/`payload`），按事件标识符分发到现有 Service。OneNet 推送报文的确切结构需拿到凭证后按实际抓包确定。
3. **`userId` 可信度**：清运事件里的 `userId` 是「设备当前登录清运员」，取决于设备端登录方式（扫码？刷卡？）。需与硬件方确认 id 来源，决定后端是否要二次校验该清运员归属。
4. **`reportSn` 生成规则**：需与固件方约定（建议 `devSn + 时间戳 + 投口` 拼接），确保同一条记录重传时不变。
5. **下发 API 规格**：OneNet 服务调用（同步/异步命令下发）的具体 HTTP API、鉴权（token 签名算法）待凭证与文档到位后补 `OneNetClient` 实现，并填 `application.yml` 的 `onenet` 段。
6. **物模型 schema 校验**：§5 JSON 按 OneNet 标准物模型常见格式编写，导入前需在 OneNet 控制台实测，按平台实际 schema 版本微调。

