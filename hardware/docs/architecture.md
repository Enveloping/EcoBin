# EcoBin 设备侧架构文档

> 香橙派 Zero3 网关程序 — 模块划分、组件通信与数据流转

## 一、总览：程序在做什么

香橙派 Zero3 作为**中继网关**，桥接三个世界：

```
┌──────────────┐     MQTT (OneNet 物模型)      ┌──────────────┐
│  云端平台    │ ◄──────────────────────────► │  香橙派 Zero3 │
│  (OneNet)    │   属性/事件/服务调用            │  (本程序)     │
└──────────────┘                               └──────┬───────┘
                                                     │
                                              串口 UART (GPIO)
                                                     │
                                            ┌────────┴───────┐
                                            │  垃圾桶 MCU     │
                                            │  (传感器+门控)   │
                                            └────────────────┘
                                                     │
                                               USB 摄像头 ×2
                                              (箱外 + 箱内)
```

**上行数据流**（设备 → 云）：MCU 上报传感器数据 → 香橙派解析 → MQTT 上报属性/事件

**下行数据流**（云 → 设备）：平台下发服务调用 → 香橙派分发处理 → 控制 MCU 开门/拍照/上传 COS → 回复结果

---

## 二、模块全景图

```
                    ┌──────────────────────────────┐
                    │         main.py               │
                    │   SmartBinGateway (组装器)     │
                    │  - 创建所有实例                  │
                    │  - 注入依赖                     │
                    │  - 连接回调                     │
                    │  - 管理生命周期                  │
                    └──────┬───────────┬────────────┘
                           │           │
              ┌────────────┘           └────────────┐
              ▼                                     ▼
   ┌──────────────────┐                  ┌──────────────────┐
   │  mqtt_gateway.py │                  │ hardware_layer.py│
   │  MqttGateway     │                  │  SerialBridge    │
   │  - 连接/鉴权      │                  │  DualCamera      │
   │  - 订阅/发布      │                  │  CosUploader     │
   │  - 消息路由       │                  │  BinState        │
   └────────┬─────────┘                  └────────┬─────────┘
            │                                     │
            │  回调注入                             │  被 door_flow
            ▼                                     │  和 handler 调用
   ┌──────────────────┐                           │
   │ thing_model.py   │                           │
   │ ThingModel       │                           │
   │  - 属性读写       │                           │
   │  - 事件上报       │◄──────────────────────────┘
   │  - 服务分发       │
   └────────┬─────────┘
            │  分发给
            ▼
   ┌──────────────────────────────┐
   │  delivery_handler.py         │
   │  clean_handler.py            │
   │  (业务处理器)                  │
   │  - 调用 door_flow 执行硬件操作 │
   │  - 调用 thing_model 上报事件   │
   └──────────┬───────────────────┘
              │  调用
              ▼
   ┌──────────────────┐     ┌──────────────────┐
   │  door_flow.py    │     │  test_mode.py    │
   │  execute_door_   │     │  MockSerialBridge│
   │  cycle()         │     │  MockCamera      │
   │  wait_for_weight()│    │  (测试模式替换)    │
   └──────────────────┘     └──────────────────┘
```

---

## 三、逐模块说明

### 3.1 `config.py` — 配置中心

**职责**：从环境变量/`.env` 文件加载所有配置项。

**对外接口**：导出所有配置常量（`PRODUCT_ID`, `DEVICE_NAME`, `DEVICE_KEY`, `MQTT_HOST`, `SERIAL_PORT`, `TEST_MODE` 等），以及 `validate()` 校验函数。

**数据流向**：`.env` 文件 → `dotenv.load_dotenv()` → `os.getenv()` → 模块级常量。其他模块 `from config import ...` 直接使用。

**关键点**：`override=True` 确保 `.env` 覆盖系统环境变量；所有值在 import 时即计算完毕（无惰性加载）。

---

### 3.2 `hardware_layer.py` — 硬件抽象层

这是最底层的模块，封装了三种硬件访问 + 一个共享状态容器：

| 类 | 职责 | 对外关键方法 |
|---|---|---|
| `BinState` | 线程安全的垃圾桶状态快照（类级别单例） | `update_weight()`, `update_overflow()`, `update_smoke()`, `get_state()`, `get_door_state()`, `set_clean_order_id()` |
| `SerialBridge` | 香橙派 ←→ MCU 串口通信 | `open()`, `close()`, `send_cmd()`（发门控指令）, `recv_loop()`（收传感器数据） |
| `DualCamera` | USB 双摄像头拍照 | `capture(device_id, path)`, `capture_both(prefix)` |
| `CosUploader` | 腾讯云 COS 图片上传 | `creds_from_cos_token()`, `upload(creds, local_path, key)` |

#### BinState 数据结构

```python
# 6 个舱门，每个舱门一个 dict
doors = [
    {"weight": 0.0, "fullness": 0, "spill_alarm": False, "smoke_alarm": False},
    # ... ×6
]
# 清运订单追踪（每门最多一个活跃清运）
_clean_order_ids = [None, None, None, None, None, None]
```

所有读写通过 `threading.Lock` 保护，`get_state()` 返回深拷贝（避免锁外修改）。

#### MCU 串口协议

```
MCU → 香橙派（传感器上报）:
  A1,{flag},A0       溢满标志（0=正常, 1=满溢）
  B1,{weight_g},B0   重量（克，整数）
  C1,{flag},C0       烟雾报警（0=正常, 1=报警）

香橙派 → MCU（门控指令）:
  D1,{door_index},{cmd},D0    cmd: open / close / status
```

`SerialBridge._parse_line()` 用正则提取各协议帧；`recv_loop()` 在独立线程中运行，处理粘包/拆包。

#### 拍照回退链

`DualCamera.capture()` → OpenCV（首选）→ `fswebcam` 系统命令（回退）→ 失败返回 False

#### COS 上传凭证

`CosUploader.creds_from_cos_token()` 将平台下发的 `cosToken` 字典（含临时密钥、bucket、region）转为 `CosConfig` 可用格式。上传后拼接 `base_url + object_key` 返回完整 URL。

---

### 3.3 `mqtt_gateway.py` — MQTT 网关

**职责**：管理与 OneNet 平台的 MQTT 连接，包括鉴权 Token 生成、订阅/发布、下行消息路由。

**核心类**：`MqttGateway`

**鉴权流程**：
```
DEVICE_KEY (Base64) → hmac-sha256 签名 → OneNet Token → MQTT Password
```

**Topic 结构**（OneNet 物模型协议）：
```
$sys/{product_id}/{device_name}/thing/property/post      ← 属性上报
$sys/{product_id}/{device_name}/thing/property/get        → 属性查询（下行）
$sys/{product_id}/{device_name}/thing/property/set        → 属性设置（下行）
$sys/{product_id}/{device_name}/thing/event/post          ← 事件上报
$sys/{product_id}/{device_name}/thing/service/{id}/invoke → 服务调用（下行）
```

**消息路由**（`_on_message` 回调）：
1. 按 topic suffix 匹配 action 类型
2. 属性查询 → 调用 `on_property_get` 回调，回复 `property_get_reply`
3. 属性设置 → 调用 `on_property_set` 回调，回复 `property_set_reply`
4. 服务调用 → 调用 `on_service_call` 回调，回复 `service/{id}/invoke_reply`

**对外回调接口**（由 main.py 注入）：
- `on_service_call` — 服务调用分发
- `on_property_get` — 属性读取
- `on_property_set` — 属性写入
- `on_connected` — 连接成功通知（触发定时上报启动）

---

### 3.4 `thing_model.py` — 物模型层

**职责**：物模型的"翻译层"——将平台物模型概念映射到本地操作。是一个薄层，不包含业务逻辑。

**核心类**：`ThingModel`

**三层职责**：

| 层 | 机制 | 示例 |
|---|---|---|
| **属性读取** | `prop_read_handlers` 字典，key 是属性名，value 是读取函数 | `"doorStates"` → `_read_door_states()` → `BinState.get_state()` |
| **属性上报** | `notify_*()` 方法 → `device.post_property()` → MQTT publish | `notify_door_states()`, `notify_online()`, `notify_rssi()` |
| **事件上报** | `notify_*()` 方法 → `device.post_event()` → MQTT publish | `notify_delivery_complete()`, `notify_clean_gross()`, `notify_smoke_alarm()` |
| **服务分发** | `service_handlers` 字典 → `dispatch_service()` | `"openDeliveryDoor"` → `DeliveryHandler.handle()` |

**关键设计决策**：`ThingModel` 持有 `self.device`（即 `MqttGateway` 实例），属性/事件上报最终通过 `device.post_property()` / `device.post_event()` 走 MQTT 发出。但 `ThingModel` 不直接依赖 MQTT 协议细节——任何实现了 `post_property()` / `post_event()` 接口的对象都可以。

**当前已注册的物模型能力**：

| 属性 | 服务 | 事件 |
|---|---|---|
| `doorStates` (6门状态数组) | `openDeliveryDoor` (投递开门) | `deliveryComplete` (投递完成+照片) |
| `fwVersion` (固件版本) | `openCleanDoor` (清运开门) | `cleanGross` (清运毛重+照片) |
| `online` (在线状态) | `reboot` (远程重启) | `cleanTare` (清运皮重) |
| `rssi` (WiFi信号强度) | | `smokeAlarm` (烟雾报警) |
| `voltage` (电压) | | `spillAlarm` (满溢报警) |

---

### 3.5 `door_flow.py` — 通用开门闭环

**职责**：纯函数式的硬件操作序列，不依赖 ThingModel 或 MQTT。投递和清运共享同一套流程。

**核心函数**：`execute_door_cycle()` — 6 步硬件闭环

```
Step 1: 串口开门        serial.send_cmd(door_index, "open")
Step 2: 拍开门照片       camera.capture_both(prefix) → 箱外+箱内
Step 3: 等待重量稳定     wait_for_weight() — 轮询 BinState，两次读数差<200g
Step 4: 串口关门        serial.send_cmd(door_index, "close")
Step 5: 拍关门照片       camera.capture_both(prefix) → 箱外+箱内
Step 6: 上传COS         uploader.upload() ×4 → 返回 4 张照片 URL
```

**重量稳定判定**（`wait_for_weight`）：
```
while 未超时:
    w1 = BinState.get_door_state(i)["weight"]
    sleep(1s)
    w2 = BinState.get_door_state(i)["weight"]
    if |w2 - w1| < 0.2kg:   ← 连续两次读数差 <200g → 稳定
        return w2
超时 → 返回最后一次读数（可能为 0）
```

**依赖注入设计**：`execute_door_cycle()` 所有依赖（serial, camera, uploader, bin_state）均通过参数传入，而非 import 固定类。这使得测试模式下可以直接传入 Mock 类。

---

### 3.6 `delivery_handler.py` — 投递开门处理器

**职责**：处理 `openDeliveryDoor` 服务调用。

**数据流**（一次完整投递）：
```
平台下发 openDeliveryDoor {doorIndex, cosToken}
  → MqttGateway._on_message() 识别为服务调用
    → on_service_call 回调 → ThingModel.dispatch_service()
      → DeliveryHandler.handle(params)
        → execute_door_cycle()  ← 硬件闭环
        → tm.notify_delivery_complete()  ← 上报投递完成事件
        → 返回 {"accepted": True}
```

---

### 3.7 `clean_handler.py` — 清运开门处理器

**职责**：处理 `openCleanDoor` 服务调用。

**数据流**（一次完整清运）：
```
平台下发 openCleanDoor {doorIndex, cleanOrderId, cosToken}
  → ... (同上路由)
    → CleanHandler.handle(params)
      → execute_door_cycle()  ← 硬件闭环
      → tm.notify_clean_gross()  ← 上报毛重事件（含 4 张照片 URL）
      → BinState.set_clean_order_id()  ← 记录活跃清运订单 ID
      → 返回 {"accepted": True}
```

**与投递的关键差异**：
- 参数多一个 `cleanOrderId`（平台下发的清运订单 ID）
- 上报事件不同：`cleanGross`（毛重）vs `deliveryComplete`
- 额外记录 `clean_order_id` 到 `BinState`，供后续皮重检测使用（注：当前皮重检测逻辑尚未完全实现，仅毛重事件已闭环）

**`handle_reboot()`**：远程重启处理器（独立函数，不属 CleanHandler），当前仅记录日志不实际执行。

---

### 3.8 `test_mode.py` — 测试模式模拟

**职责**：当 `TEST_MODE=true` 时，提供 MCU 硬件的模拟实现。

| 类 | 模拟对象 | 关键行为 |
|---|---|---|
| `MockSerialBridge` | `SerialBridge` | `open()` 恒返回 True；`send_cmd("open")` 即时注入 500-5000g 随机重量到 `BinState`；`recv_loop()` 空转 |
| `MockCamera` | `DualCamera` | `capture()` 生成最小有效 JPEG（1×1 像素，约 160 字节）；`capture_both()` 生成两张 |

**不做模拟的部分**：`CosUploader` 和 `MqttGateway` 始终走真实实现。测试模式仅消除对物理硬件的依赖（串口、摄像头），云侧交互完整保留。

**注入机制**（在 `main.py` 的 `SmartBinGateway.__init__` 中）：
```python
if TEST_MODE:
    SerialCls = MockSerialBridge
    CameraCls = MockCamera
else:
    SerialCls = SerialBridge
    CameraCls = DualCamera

self.serial = SerialCls()          # ← 根据 TEST_MODE 选择实现
self.delivery_handler = DeliveryHandler(
    serial=self.serial,
    camera=CameraCls,              # ← 类本身作为参数，不是实例
    uploader=CosUploader,          # ← 始终真实
    ...
)
```

**模拟重量注入的时序**：`MockSerialBridge.send_cmd("open")` 同步注入重量到 `BinState`，然后 `execute_door_cycle()` 中的 `wait_for_weight()` 在后续轮询时立即读到有效重量，保证稳定判定通过。

---

### 3.9 `main.py` — 入口与组装器

**职责**：`SmartBinGateway` 类负责组装所有模块、注入依赖、连接回调、管理生命周期。

**不包含业务逻辑** — 所有逻辑在子模块中，main.py 只做"接线"。

#### 初始化流程（`__init__`）

```
1. 创建 exit_flag (threading.Event)
2. 校验配置
3. 打印测试模式提示
4. 选择 SerialCls / CameraCls (根据 TEST_MODE)
5. 创建 serial 实例，绑定传感器回调
6. 创建 MqttGateway 实例
7. 创建 ThingModel 实例
8. 创建 DeliveryHandler / CleanHandler 实例（注入 serial, camera, uploader, tm）
9. 向 MqttGateway 注册回调（on_service_call, on_property_get 等）
10. 向 ThingModel 注册 service_handlers
11. 注册信号处理器（SIGINT, SIGTERM）
```

#### 依赖注入关系图

```
SmartBinGateway
├── serial: SerialBridge (或 MockSerialBridge)  ──┐
├── gw: MqttGateway                               │
├── tm: ThingModel(gw)                            │ 注入到 DeliveryHandler
│       ├── service_handlers = {                  │ 和 CleanHandler
│       │     "openDeliveryDoor": deliv.handle,   │
│       │     "openCleanDoor":    clean.handle,   │
│       │     "reboot":           handle_reboot,  │
│       │   }                                     │
│       └── prop_read_handlers = {...}            │
├── delivery_handler: DeliveryHandler( ◄──────────┘
│       serial, camera, uploader, bin_state, tm, device_name)
└── clean_handler: CleanHandler(
        serial, camera, uploader, bin_state, tm, device_name)
```

#### 回调链（下行：云 → 硬件）

```
MQTT message (OneNet)
  → gw._on_message()
    → gw.on_service_call(svc_id, params, msg_id)   [回调]
      → tm.dispatch_service(svc_id, params, msg_id)
        → tm.service_handlers[svc_id](params)      [即 handler.handle()]
          → execute_door_cycle()                   [硬件闭环]
          → tm.notify_xxx()                        [MQTT 上报事件]
```

#### 回调链（上行：传感器 → 云）

```
MCU 串口数据
  → serial.recv_loop()
    → serial._parse_line()
      → BinState.update_weight/overflow/smoke()
      → serial.on_weight_received(door, grams)    [回调 → main._on_weight_from_mcu]
      → serial.on_spill_alarm(door)               [回调 → main._on_spill_from_mcu]
        → tm.notify_spill_alarm()                 [MQTT 上报]
      → serial.on_smoke_alarm(door)               [回调 → main._on_smoke_from_mcu]
        → tm.notify_smoke_alarm()                 [MQTT 上报]
```

#### 生命周期

```
run()
  ├── 校验凭证
  ├── serial.open() → 启动 recv_loop 线程
  ├── gw.connect() → 连接 OneNet MQTT
  │     └── _on_connect() → gw.on_connected() → _start_periodic_report()
  │           └── 启动 _report_loop 线程 (daemon)
  ├── gw.loop_forever()  ← 主线程阻塞在这里
  │     └── 收到 SIGINT/SIGTERM
  │           └── _signal_handler()
  │                 ├── exit_flag.set()     (通知定时上报线程退出)
  │                 └── gw.disconnect()      (使 loop_forever 退出阻塞)
  └── finally: _shutdown()
        ├── exit_flag.set()
        ├── serial.close()
        └── gw.disconnect()
```

---

## 四、数据流转场景

### 场景 A：定时属性上报（每 30 秒）

```
_report_loop 线程 (daemon)
  → tm.notify_door_states(tm._read_door_states())
      → BinState.get_state() → 6门状态快照
      → tm._prop_notify("doorStates", value)
        → gw.post_property({doorStates: {value, time}})
          → MQTT publish → OneNet 平台

  → tm.notify_online(gw.connected)   → MQTT publish
  → tm.notify_rssi(-40)              → MQTT publish
  → tm.notify_voltage(5.0)           → MQTT publish
```

### 场景 B：投递开门（完整链路）

```
时间线                    组件                        数据
──────────────────────────────────────────────────────────────
T0  平台下发           OneNet → MQTT              {doorIndex, cosToken}
T1  MQTT 路由          gw._on_message()           识别为 service/invoke
T2  回调分发           gw.on_service_call()       → tm.dispatch_service()
T3  服务匹配           tm.service_handlers        匹配到 DeliveryHandler.handle
T4  开门               serial.send_cmd("open")    → MCU 开锁
T5  拍照               camera.capture_both()      → 箱外/箱内 JPEG
T6  等重量             wait_for_weight()          轮询 BinState（最长 15s）
T7  关门               serial.send_cmd("close")   → MCU 关锁
T8  拍照               camera.capture_both()      → 箱外/箱内 JPEG
T9  COS 上传           uploader.upload() ×4       → 4 个图片 URL
T10 事件上报           tm.notify_delivery_         → MQTT publish
                        complete()
T11 回复平台           gw._publish(reply_topic)    {"code": 200, ...}
```

### 场景 C：测试模式下的投递

与场景 B 的差异仅在 T4 和 T5：

```
T4' Mock 开门         MockSerialBridge.send_cmd("open")
                      → 随机重量 500-5000g 写入 BinState  ← 同步注入
T5' Mock 拍照         MockCamera.capture_both()
                      → 160 字节占位 JPEG
```

其余步骤（COS 上传、MQTT 上报）完全一致。

---

## 五、关键设计决策

### 5.1 依赖注入优于硬编码导入

`door_flow.execute_door_cycle()` 的所有依赖通过参数传入，不直接 import 具体类。Handler 也在构造时接收依赖。这带来两个好处：

1. **可测试性**：测试模式下可以替换 SerialBridge 和 DualCamera 为 Mock
2. **松耦合**：door_flow 不知道也不关心串口协议细节

### 5.2 类级别共享状态（BinState）

`BinState` 所有字段和方法都是 `@classmethod`，本质上是类级别的单例。设计理由：

- 多个线程需要读写同一份传感器数据（串口线程写入，door_flow 线程读取）
- 避免在构造器链中传递 `BinState` 实例
- `threading.Lock` 保证线程安全

代价是全局可变状态，但在这个嵌入式场景中（单进程、单设备），复杂度和收益是匹配的。

### 5.3 回调模式连接方向

```
硬件层 ──回调──► main.py ──直接调用──► thing_model ──方法调用──► mqtt_gateway
```

传感器数据通过回调"向上冒泡"到 main.py，main.py 决定是否需要上报。MQTT 下行消息也通过回调分发，但用的是"注册 handler"模式（`service_handlers` 字典）而非回调函数。

### 5.4 测试模式最小化原则

仅模拟 MCU 硬件（串口 + 摄像头），不模拟网络层（MQTT + COS）。理由：

- MQTT 和 COS 有真实的云侧端点可以连接，不需要模拟
- 串口和摄像头依赖物理硬件（香橙派 GPIO / USB），开发机上无法测试
- 这个边界划分使得测试模式贴近真实运行环境，同时消除硬件依赖

### 5.5 信号处理与优雅退出

```
SIGINT/SIGTERM → _signal_handler()
  ├── exit_flag.set()      → _report_loop 线程在下次 wait(30) 时退出
  └── gw.disconnect()      → paho 检测到 _state == disconnecting → loop_forever() 退出
                               → 控制权回到 run() → finally: _shutdown()
```

两个退出路径必须同时触发：`exit_flag` 让定时线程停下，`gw.disconnect()` 让主线程从 `loop_forever()` 阻塞中退出。缺一个都会导致无法正常退出。

---

## 六、文件清单

```
hardware/
├── main.py                入口 + 组装器 (SmartBinGateway)
├── config.py              配置中心（.env → Python 常量）
├── mqtt_gateway.py        MQTT 网关（连接/鉴权/收发/路由）
├── thing_model.py         物模型层（属性读写/事件上报/服务分发）
├── door_flow.py           通用开门闭环（开→拍→称→关→拍→传）
├── delivery_handler.py    投递开门处理器
├── clean_handler.py       清运开门处理器 + reboot handler
├── hardware_layer.py      硬件抽象层（串口/摄像头/COS/BinState）
├── test_mode.py           测试模式模拟（MockSerialBridge / MockCamera）
├── .env.example           环境变量模板
├── .env                   实际凭证（gitignore）
├── pyproject.toml         Python 项目配置 (uv)
├── uv.lock                锁文件
└── docs/
    └── architecture.md    ← 本文件
```

---

## 七、线程模型

| 线程 | 名称 | 创建位置 | 生命周期 | 职责 |
|---|---|---|---|---|
| **主线程** | MainThread | `__main__` | 程序全周期 | `gw.loop_forever()` 阻塞，处理 MQTT 回调 |
| **串口接收** | `serial_recv` (daemon) | `main.run()` | 串口打开 → 程序退出 | 持续读取 MCU 数据，解析协议，更新 BinState |
| **定时上报** | `periodic` (daemon) | `_start_periodic_report()` | MQTT 连接成功 → exit_flag 置位 | 每 30 秒上报一次属性（门状态/在线/RSSI/电压） |

所有线程通过以下机制协调：

- **BinState.lock**：保护传感器共享数据
- **exit_flag (threading.Event)**：通知定时上报线程退出
- paho 内部状态机：disconnect() 使 loop_forever() 退出阻塞
