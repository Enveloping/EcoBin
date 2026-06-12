# 用 MQTTX 模拟设备走完整 OneNet 上行链路

> 目的：在没有真实硬件的情况下，用自己的电脑（MQTTX 客户端）扮演一台 OneNet 设备，
> 发一条物模型事件，验证后端**北向 MQ 上行链路**：
> `设备 MQTT → OneNet 平台 → 北向消费组(Pulsar) → OneNetMqConsumer → 解密 → OneNetEventDispatcher → reportGross/...`
>
> 关联实现：`framework/onenet/OneNetMqConsumer`、`OneNetCipher`、`business/onenet/OneNetEventDispatcher`。
> 报文/连接参数均取自 OneNet 官方文档（MQTT 设备连接、物模型数据交互、服务端订阅消息类型）。

---

## 0. 准备清单

| 需要 | 说明 |
|------|------|
| MQTTX | MQTT 桌面客户端（https://mqttx.app），用作"设备" |
| OneNet 控制台账号 | 与 `.env` 里消费组（`iotAccessId`）同一账号 |
| 一个 MQTT/OneJSON 产品 | 没有就新建（见 A.1） |
| 一台测试设备 | `设备名 = sn`，拿到 `产品ID` + `设备key`（见 A.3） |
| 本机后端 + MySQL | 后端带 `.env` 启动，消费者连上 OneNet（见 B） |

---

## A. OneNet 控制台准备

### A.1 创建产品
「产品开发」→ 新建：**智能化方式=设备接入类，接入协议=MQTT，数据协议=OneJSON** → 记下 **产品ID（pid）**。

### A.2 导入物模型
进入产品「功能定义 / 设置物模型」，导入 `docs/onenet-thing-model.json`。
> 只为打通链路的话，至少要有 `cleanGross` 事件功能点（输出 `cleanOrderId` int64 + `weight` float）。
> 物模型字段口径见 `docs/onenet-thing-model.md` §4。

### A.3 创建设备
「设备管理」→ 新建设备：**设备名称 = 一个 sn**。
- 阶段2（业务落地）要求这个 sn 与后端 `biz_device.sn` 对应，**建议直接用一台真实/测试设备的 sn**。
- 设备详情页拿到 **设备key**（用于算 token）。

### A.4 配置服务端订阅（关键！否则消费者收不到）
「数据流转 → 服务端订阅」：把 `.env` 里的消费组（`iotAccessId`）**绑定到本产品**，消息类型勾选 **设备数据上报（thingEvent）**。
> 北向消费组只会收到"已配置订阅"的产品的消息。漏了这步 = MQTTX 发得出去、后端啥也收不到。

### A.5 生成连接 token
用官方「Token 生成工具」（接入安全认证页）：
- `res = products/{pid}/devices/{deviceName}`
- `key = 设备key`
- 有效期设长一点（如几天），算出的字符串就是 MQTT 的 **Password**。

---

## B. 启动后端并确认消费者已连上

1. 改了多模块后先装产物（避免跑旧 jar）：
   ```bash
   ./mvnw install -DskipTests
   ```
2. 启动后端（需本机 MySQL 在 `localhost:3306` 运行）。
3. **确认 `.env` 被加载**——`application.yml` 用 `spring.config.import: optional:file:./.env[.properties]`，是**相对路径**，依赖**运行工作目录 = 项目根**（`.env` 所在目录）。
   - ✅ 日志出现：`[OneNet·MQ] 北向消费者已启动 broker=pulsar+ssl://iot-north-mq.heclouds.com:6651/, accessId=..., subscription=...` → 已连上。
   - ❌ 日志是：`[OneNet·MQ] 消费组凭证未配置（accessId/secretKey/subscriptionName），跳过北向消费者启动` → `.env` 没被加载：
     - 把运行配置的 **working directory 设为项目根**（IDEA：Run/Debug Configurations → Working directory）；
     - 或临时用 JVM 参数传入：`-DiotAccessId=... -DiotSecretKey=... -DiotSubscriptionName=...`。

---

## C0.（最简，推荐先试）用控制台「设备模拟器」上报

OneNet 控制台自带**设备模拟器**（产品开发 → 设备调试 / 在线调试，选「设备模拟器」上报方向），
以设备身份直接发物模型数据，**无需 MQTTX、无需算 token**。

本次测试设备：
| 项 | 值 |
|----|----|
| 设备名称（= 后端的 `sn`） | `test-divice-1` |
| 设备ID | `ejFaUTZMUTQwajVaakhhb3NqUTVXVERCanhTd1JQYnc=` |

> 注意：后端按 **设备名称（deviceName）** 当 `sn` 反查设备，设备ID 这串不参与后端逻辑。
> 设备名是 `test-divice-1`（带笔误的拼写也照原样用，别自行改成 device）。

步骤：
1. 在模拟器里选「上报方向 / 设备上报」，功能点选 `cleanGross` 事件，填参数 `cleanOrderId`、`weight`，提交。
   - 若模拟器只给原始 OneJSON 输入框，就贴 C.3 的 `cleanGross` payload。
2. 看后端日志（同 D 阶段1）：出现 `[OneNet·MQ] 收到上行明文：{...}` 即链路通。

⚠ 若模拟器发出后**后端收不到**：八成是该模拟器只在平台内部回显、未触发数据流转，或 A.4 的服务端订阅没绑产品。
先确认 A.4；仍不行就退回下面的 **C. MQTTX**（真实设备连接一定会触发北向转发）。

---

## C. MQTTX 连接 + 发事件（C0 走不通时用）

### C.1 新建连接
| 字段 | 值 |
|------|----|
| Host | `mqtts.heclouds.com`（明文）或 `mqttstls.heclouds.com`（TLS） |
| Port | `1883`（明文）/ `8883`（TLS，需下证书） |
| Client ID | **设备名（deviceName = sn）** |
| Username | **产品ID（pid）** |
| Password | **A.5 算出的 token** |

连接成功后，控制台「设备管理」里该设备应显示**在线**。

### C.2 （可选）订阅回执
订阅 `$sys/{pid}/{device-name}/thing/event/post/reply`，发完事件能看到平台回 `success`。

### C.3 发布事件
发布 topic：`$sys/{pid}/{device-name}/thing/event/post`（把 `{pid}`/`{device-name}` 换成真实值）。

**cleanGross（清运毛重）**：
```json
{"id":"1700000000001","version":"1.0","params":{"cleanGross":{"value":{"cleanOrderId":123,"weight":12.5},"time":1700000000000}}}
```

**cleanTare（换新空袋去皮）**：
```json
{"id":"1700000000002","version":"1.0","params":{"cleanTare":{"value":{"cleanOrderId":123,"weight":0.30},"time":1700000000000}}}
```

**deliveryComplete（投递完成·上传后建单）**：须带 `doorIndex`；照片 4 个 URL 由设备直传 COS 后随本事件回传（可选，缺失则前端占位）。已去 `deliveryToken`（无幂等键，勿重发）。
```json
{"id":"1700000000003","version":"1.0","params":{"deliveryComplete":{"value":{"doorIndex":1,"weight":3.2,"wasteType1":1,"wasteType2":11,"photoOpenOutside":"https://ecobin-1258140596.cos.ap-shanghai.myqcloud.com/SN-1/1/d-abc/open_outside.jpg","photoOpenInside":"https://ecobin-1258140596.cos.ap-shanghai.myqcloud.com/SN-1/1/d-abc/open_inside.jpg","photoCloseOutside":"https://ecobin-1258140596.cos.ap-shanghai.myqcloud.com/SN-1/1/d-abc/close_outside.jpg","photoCloseInside":"https://ecobin-1258140596.cos.ap-shanghai.myqcloud.com/SN-1/1/d-abc/close_inside.jpg"},"time":1700000000000}}}
```
> 后端收到此事件才建单：需先用小程序对该设备「开启设备」建立活跃会话，否则建无主单不返现（见 `onenet-thing-model.md` §8）。

> 事件输出统一被 `value` 包裹（OneJSON 规范），后端 `OneNetEventDispatcher.unwrap` 已兼容。
> `id` 13 位以内字符串数字；`cleanOrderId/weight` 按需替换。

---

## D. 验证（分两阶段）

### 阶段1 · 链路连通（无需预置数据）
发完 `cleanGross`，后端日志应出现：
```
[OneNet·MQ] 收到上行明文：{"msgType":"thingEvent","subData":{"deviceName":"<你的sn>",...,"params":{"cleanGross":{"value":{"cleanOrderId":123,"weight":12.5},...}}}}
```
**看到这条 = 连接 / 解密 / 报文格式 / 分发入口 全部打通**——这就验证了上一阶段写的整条上行链路。
- 紧接着分发器会调 `reportGross`；若数据库里没有 id=123 的清运单，会抛"订单不存在"，但异常被吞、只记日志（`[OneNet·分发] 处理事件 cleanGross 失败...`），**不影响"链路已通"的结论**。
- 顺便核对这条真实明文里 `cleanGross` 是不是 `{"value":{...},"time":...}` 结构（应当是）。

### 阶段2 · 业务真正落地
1. 确保 `biz_device` 有一台 `sn = 你的 deviceName` 的设备（同租户）。
2. 走小程序 / 接口 `POST /api/app/clean/open`（扫新空袋开清运门）拿到真实 **cleanOrderId**。
3. 用该 id 发 `cleanGross` → 查清运单 `gross_weight / net_weight` 已回填（`net = 毛重 − 该投口当前去皮`）。
4. 再发 `cleanTare`（同 cleanOrderId）→ `biz_clean_bag` 该投口去皮更新为新袋。

---

## E. 排错表

| 现象 | 可能原因 / 处理 |
|------|----------------|
| MQTTX 连不上（鉴权失败） | token 算错或过期；`res` 不是 `products/{pid}/devices/{deviceName}`；ClientID 不是设备名、Username 不是 pid |
| 设备显示离线 | 同上；或 Host/Port 选错（明文用 1883、TLS 用 8883+证书） |
| 发布成功但后端无日志 | **服务端订阅没绑该产品**（A.4）；或消息类型没勾 thingEvent；或后端消费者没启动（看 B 的日志判断） |
| 后端日志 `消费组凭证未配置...跳过` | `.env` 未加载，按 B.3 修工作目录或传 JVM 参数 |
| 收到明文但 `reportGross 失败` | 阶段1 正常现象（订单不存在）；阶段2 请按 D 预置真实 cleanOrderId |
| 收到明文但事件没进 reportGross | 核对明文里 identifier 拼写（`cleanGross/cleanTare/deliveryComplete`）与 topic 是否为 `.../thing/event/post` |

---

> 下行（开门命令）不在本文档范围：需 OneNet 平台 API 凭证（`product-id/access-key`），未到位，见 `docs/open-items.md`。
