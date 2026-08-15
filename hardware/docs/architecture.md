# EcoBin 设备侧架构文档

> 香橙派 Zero3 网关程序 — 模块划分、组件通信与数据流转

首次自动注册、维护 CA 与 OneNet 按需反向 SSH 的运行和部署边界见
[`enrollment-and-remote-support.md`](enrollment-and-remote-support.md)。

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
   ┌──────────────────┐     ┌────────────────────────┐
   │  door_flow.py    │     │ 显式硬件模拟边界         │
   │  execute_delivery│     │ Linux PTY 虚拟 MCU      │
   │  _cycle()        │     │ simulated:// 双摄源      │
   │  execute_door_   │     │ 与真机使用相同适配入口    │
   │  cycle() (旧清运) │     │                        │
   └──────────────────┘     └────────────────────────┘
```

---

## 三、逐模块说明

### 3.1 `config.py` — 配置中心

**职责**：从环境变量/`.env` 文件加载所有配置项。

**对外接口**：导出所有配置常量（`PRODUCT_ID`, `DEVICE_NAME`, `DEVICE_KEY`, `MQTT_HOST`, `SERIAL_PORT` 等），以及 `validate()` 校验函数。

**数据流向**：`.env` 文件 → `dotenv.load_dotenv()` → `os.getenv()` → 模块级常量。其他模块 `from config import ...` 直接使用。

**关键点**：`override=True` 确保 `.env` 覆盖系统环境变量；所有值在 import 时即计算完毕（无惰性加载）。

---

### 3.2 `hardware_layer.py` — 硬件抽象层

这是最底层的模块，封装了三种硬件访问 + 一个共享状态容器：

| 类 | 职责 | 对外关键方法 |
|---|---|---|
| `BinState` | 线程安全的垃圾桶状态快照（类级别单例） | `update_weight()`, `update_overflow()`, `update_smoke()`, `get_state()`, `get_door_state()`, `set_clean_order_id()` |
| `SerialBridge` | 香橙派 ←→ MCU 串口通信 | `send_door_control()`, `send_price_digit()`, `wait_for_door_state()`, `wait_for_weight()` |
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
MCU → 香橙派（固定长度二进制帧）:
  AA,{0|1},AA          门状态（0=关盖, 1=开盖）
  BB,{0|1},BB          红外状态（0=未遮挡, 1=遮挡/满溢）
  CC,{0|1},CC          烟雾报警
  DD,{uint24-be},DD    本次最终重量（克，3 字节大端）

香橙派 → MCU:
  AA,00,AA             开盖
  AA,01,AA             关盖
  BB,{0..9},BB         0.0–0.9 元/kg 的一位单价
```

`SerialBridge` 按帧头对应的固定长度解析二进制流，处理拆包、粘包、噪声重同步并限制接收缓冲区。
当前只支持物理投口 1；旧 `send_cmd()` 仅供未适配新固件的清运代码保留。

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
| `unitPrice` (可读写单价) | | |

---

### 3.5 `door_flow.py` — 投递与旧清运流程

`execute_delivery_cycle()` 使用新版二进制协议：发送开盖 → 等待 MCU 开盖状态 → 拍照 →
等待本次新的最终重量帧 → 发送并确认关盖 → 拍照上传。事件版本号保证历史门状态和重量不能
满足本次等待。失败时不上报完成事件，并额外发送一次关盖命令安全收尾。

`execute_door_cycle()` 仅为当前旧清运代码保留，仍使用 D1 文本协议和旧重量轮询；它尚未
适配新版 MCU 固件，后续应随清运 v3 一并替换。

**依赖注入设计**：两个流程的串口、摄像头和上传器均由 Handler 注入，使单元测试可以显式替换为测试替身。

---

### 3.6 `delivery_handler.py` — 投递开门处理器

**职责**：处理 `openDeliveryDoor` 服务调用。

**数据流**（一次完整投递）：
```
平台下发 openDeliveryDoor {doorIndex, cosToken}
  → MqttGateway._on_message() 识别为服务调用
    → on_service_call 回调 → ThingModel.dispatch_service()
      → DeliveryHandler.handle(params)
        → execute_delivery_cycle()  ← 新二进制协议硬件闭环
        → tm.notify_delivery_complete()  ← 上报投递完成事件
        → 返回 {"accepted": True}
```

---

### 3.7 `clean_handler.py` — 清运开门处理器

**职责**：处理 `openCleanDoor` 服务调用。

> 当前仍是旧 D1 文本协议路径，未适配新版 MCU 固件；保留代码仅用于隔离本次投递改造，
> 不能作为清运已联通的依据。

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

### 3.8 显式硬件模拟边界

正式 `main.py` 没有全局模拟开关，也不会按运行模式替换串口或摄像头实现：

- MCU 模拟器通过 Linux PTY 暴露串口路径，由 `ECOBIN_SERIAL_PORT` 选择；
- 摄像头模拟器通过两个不同的 `simulated://` 显式源接入；
- MQTT、SQLite、COS、命令处理和事件投影始终运行真实代码路径；
- 自动化单元测试仍可直接注入 `MockUartLink`、模拟拍照函数等局部测试替身。

这样端到端测试与真机只更换设备路径，不更换应用组装逻辑。

---

### 3.9 `main.py` — 入口与组装器

**职责**：`SmartBinGateway` 类负责组装所有模块、注入依赖、连接回调、管理生命周期。

**不包含业务逻辑** — 所有逻辑在子模块中，main.py 只做"接线"。

#### 初始化流程（`__init__`）

```
1. 创建 exit_flag 与 UnitPriceStore（损坏/缺失时回退 0.5）
2. 校验配置并选择测试/真实硬件类
3. 创建 serial 实例并绑定传感器回调
4. 创建 MqttGateway 与 ThingModel（注册 unitPrice 读写）
5. 创建 DeliveryHandler / CleanHandler 实例
6. 注册 MQTT 回调、服务处理器和系统信号
7. 串口打开后同步当前单价并启动接收线程
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
          → execute_delivery_cycle()/execute_door_cycle()
                                                    [投递新协议/清运旧协议]
          → tm.notify_xxx()                        [MQTT 上报事件]
```

#### 回调链（上行：传感器 → 云）

```
MCU 串口数据
  → serial.recv_loop()
    → serial._feed_received_data()
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
T4  开门               send_door_control(True)    → 等 MCU 上报 AA 01 AA
T5  拍照               camera.capture_both()      → 箱外/箱内 JPEG
T6  等重量             serial.wait_for_weight()   等本次 DD 帧（默认最长 120s）
T7  关门               send_door_control(False)   → 等 MCU 上报 AA 00 AA
T8  拍照               camera.capture_both()      → 箱外/箱内 JPEG
T9  COS 上传           uploader.upload() ×4       → 4 个图片 URL
T10 事件上报           tm.notify_delivery_         → MQTT publish
                        complete()
T11 回复平台           gw._publish(reply_topic)    {"code": 200, ...}
```

### 场景 C：外部虚拟设备下的投递

与场景 B 的差异仅在设备路径：

```
T4' PTY MCU           FixedFrameMcuAdapter → PTY → DD/EF
T5' 显式模拟摄像头    PhotoManager → simulated://... → JPEG
```

其余步骤（COS 上传、MQTT 上报）完全一致。

---

## 五、关键设计决策

### 5.1 依赖注入优于硬编码导入

`door_flow` 流程的硬件依赖通过参数传入，不固定具体实现。Handler 也在构造时接收依赖。这带来两个好处：

1. **可测试性**：单元测试可显式注入替身，端到端测试可接入 PTY 和模拟摄像头源
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

### 5.4 不设置全局模拟分支

应用只识别串口协议、串口路径和摄像头源，不识别“生产/测试”运行模式。MCU 通过
操作系统 PTY 接入，摄像头通过显式 `simulated://` 源生成占位 JPEG；两者都由各自
配置选择，避免全局组装分支。MQTT 和 COS 是否连接测试环境由各自的显式端点和凭证
决定。

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
├── door_flow.py           新投递状态机 + 旧清运开门闭环
├── device_config.py       unitPrice 校验、转换与原子持久化
├── delivery_handler.py    投递开门处理器
├── clean_handler.py       清运开门处理器 + reboot handler
├── hardware_layer.py      硬件抽象层（串口/摄像头/COS/BinState）
├── test_mode.py           仅供单元测试/调试工具显式注入的替身
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
