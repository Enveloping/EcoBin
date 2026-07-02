# hardware-refactor 代码审查报告

**日期**: 2026-07-02  
**分支**: `worktree-hardware-refactor`  
**审查轮次**: 第 1 轮（初審）+ 第 2 轮（复審）

---

## 变更概览

| 文件 | 状态 | 说明 |
|------|------|------|
| `.gitignore` | 修改 | 移除 `hardware/` 忽略规则，意图将硬件脚本纳管 |
| `hardware/config.py` | 新增 | 环境变量配置中心（含必填校验） |
| `hardware/mqtt_gateway.py` | 新增 | MQTT 连接/鉴权/收发/消息路由 |
| `hardware/thing_model.py` | 新增 | 物模型：属性读写、事件上报、服务分发 |
| `hardware/door_flow.py` | 新增 | 通用开门闭环（开→拍→称→关→拍→传 COS） |
| `hardware/delivery_handler.py` | 新增 | 投递开门处理器 |
| `hardware/clean_handler.py` | 新增 | 清运开门处理器 |
| `hardware/hardware_layer.py` | 新增 | 硬件抽象层（串口/摄像头/COS/BinState） |
| `hardware/main.py` | 新增 | 入口，组装所有模块 |
| `hardware/.gitignore` | 新增 | 排除 `.venv/`、`__pycache__/`、`.env` 等 |
| `hardware/pyproject.toml` | 新增 | uv 项目配置 |
| `hardware/uv.lock` | 新增 | uv 依赖锁定 |
| `hardware/upload_image/` | 保留 | 旧脚本保留为独立工具/模拟器 |

---

## 架构评价 — 优秀

重构从原来散落的脚本变成了清晰的**模块化分层架构**：

```
main.py              ← 组装层（依赖注入、生命周期协调）
  ├── config.py      ← 配置层（环境变量 + 默认值 + 校验）
  ├── mqtt_gateway   ← 通信层（MQTT 连接/鉴权/Topic 收发）
  ├── thing_model    ← 协议层（属性读写、事件上报、服务分发）
  ├── delivery/clean_handler ← 业务层（投递/清运流程）
  ├── door_flow      ← 流程层（通用开门闭环，纯函数、可独立测试）
  └── hardware_layer ← 硬件抽象层（串口/摄像头/COS/BinState）
```

**亮点**:
- **依赖注入**到位：handler 不自己创建依赖，由 `main.py` 注入，便于单元测试
- **通用开门闭环** (`door_flow.py`) 用纯函数实现，不耦合 MQTT/物模型，投递和清运都复用它
- **BinState** 线程安全，用 `threading.Lock()` 保护所有读写
- 异常处理完善，失败场景有日志有返回值

---

## 第 1 轮审查发现

### 🔴 高危（3 项）

| # | 文件 | 问题 | 修复建议 |
|---|------|------|----------|
| 1 | `.gitignore` + `hardware/` | `.venv/` 和 `__pycache__/` 即将被纳入版本控制。`.gitignore` 删掉了 `hardware/` 的忽略规则，但没有任何新规则保护虚拟环境 | 在 `hardware/` 下加 `.gitignore` 排除 `.venv/`、`__pycache__/`、`*.pyc` |
| 2 | `config.py:27-30` | `DEVICE_KEY` 硬编码默认值 `"U0lWQ0VZc2FwOGZpNDB3ZXQ2Y2Y3RFBCd3JpMVJFcFo="`，纳入版本控制后会泄露到 git 历史 | 默认值改为空字符串，`validate()` 中检测空值报错 |
| 3 | `upload_image/onenet_device.py:61-63` | 硬编码 `PRODUCT_ID`、`DEVICE_NAME`、`DEVICE_KEY`，同样会入库 | 改为从环境变量读取，默认值用占位符 |

### 🟡 中危（3 项）

| # | 文件 | 问题 |
|---|------|------|
| 4 | `config.py:34` | MQTT 默认使用明文端口 1883，生产环境有窃听风险 |
| 5 | `hardware_layer.py:52,380` | SDK 缺失时 `CosConfig = None`，`upload()` 会抛 `TypeError` 而非友好报错 |
| 6 | `hardware_layer.py:217,227,238` | `_parse_line` 多舱门传感器数据硬编码 `door_index=0`（TODO 已标注） |

### 🟢 建议（4 项）

| # | 文件 | 问题 |
|---|------|------|
| 7 | `mqtt_gateway.py:125,138` | `post_property`/`post_event` 接受 `timeout_ms` 参数但从未使用 |
| 8 | `thing_model.py:70-74` | `dispatch_service` 不把 `msg_id` 传给 handler callback |
| 9 | `onenet_device.py:98-128` | 自写了一份 `build_token()`，与 `mqtt_gateway.build_token()` 重复实现 |
| 10 | `door_flow.py` | `wait_for_weight` 重量单位（kg vs g）在代码与注释间不够直观（逻辑正确） |

---

## 第 2 轮审查（复審）

### ✅ 已修复（3/3 高危）

| 上次 # | 修复内容 |
|--------|----------|
| 1 | 新增 `hardware/.gitignore`，排除 `__pycache__/`、`.venv/`、`.env`、`.DS_Store`、`Thumbs.db`、`/tmp/`、`*.tmp`，规则完整规范 |
| 2 | `config.py`：`DEVICE_KEY` 默认值改为 `""`；新增 `_REQUIRED = ["ECOBIN_DEVICE_KEY"]`；`validate()` 对必填项缺失打 `logger.error`（非仅 warning） |
| 3 | `onenet_device.py`：`DEVICE_KEY` 改为 `""`，`build_token()` 原有空值检查会抛 `ValueError("请先填 DEVICE_KEY...")`，行为正确 |

### 🟡 中危（未改动，不影响当前正确性）

3 项均保持原样：MQTT 明文端口、CosConfig=None 守卫缺失、多舱门硬编码 door_index=0（有 TODO 注释）。

### 🟢 建议（未改动）

4 项均保持原样：死参数、msg_id 丢失、build_token 重复实现、重量单位注释。

### 🆕 新发现

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| 11 | `upload_image/onenet_device.py` | 31 | 注释仍写着 "硬件/ 整个目录已 gitignore"，但现在 `hardware/` 已从 `.gitignore` 移除，注释与事实不符 |

---

## 当前状态总结

| 类别 | 第 1 轮 | 第 2 轮 |
|------|---------|---------|
| 🔴 高危 | 3 | 0 |
| 🟡 中危 | 3 | 3（未改动） |
| 🟢 建议 | 4 | 4（未改动） |
| 🆕 新发现 | — | 1（过期注释） |

**结论**: 可以合并进 main。高危已全部清零，`hardware/.gitignore` 写得规范，`config.py` 的必填校验做得到位。剩余中危/建议项不影响代码正确性，可作为后续迭代逐步处理。
