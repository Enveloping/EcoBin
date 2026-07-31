# UART 协议审计报告

> **当前状态（2026-07-27）**：默认入口已改为双方重新协商的 9 字节 DD/EF 固定帧
> 适配层；见
> [`../单片机-香橙派适配通信协议详细内容.md`](../单片机-香橙派适配通信协议详细内容.md)。
> 2026-07-25 的 UART 1.0 HIL 仍作为历史证据保留，但不是本分支默认生产入口。
> 下文是更早的文本协议审计，不能代表当前实现。
>
> 审计日期：2026-07-04
> 审计范围：`hardware/hardware_layer.py` SerialBridge 协议实现 + `hardware/door_flow.py` 开门闭环
> 协议版本：A1/B1/C1/D1 逗号分隔文本帧

> **历史说明（2026-07-17）**：投递链路已改用固定长度 AA/BB/CC/DD 二进制协议，
> 本报告的正则、换行分帧和旧重量轮询结论仅适用于旧实现。接收缓冲上限、严格分帧、
> 新重量事件隔离和门状态确认已在新投递路径落实；CRC、序号、独立 ACK/重试、多投口
> 及清运适配仍未完成，现状见 `uart-protocol-temporary-compatibility.md`。

---

## 严重问题 (4)

### 1. 协议无校验和，数据错误无法检测

**位置**: `hardware_layer.py:202-207`

**描述**: 三个上行帧（A/B/C）均为裸数据，不含任何校验字段。UART 线路上一个 bit 翻转即可导致数值错误，接收方无法感知。

```
正常:   B1,1500,B0
翻转:   B1,1501,B0   (+1g，发现不了)
翻转:   B1,15x0,B0   (乱码，try/except 吞掉)
```

垃圾桶内部有电机（电磁干扰源），传输错误概率不低。

**建议**: 加简单的校验字段，最低成本方案是追加一个校验和字节或校验数字：

```
B1,1500,42,B0    ← 42 = (1500 % 100)，接收方校验
```

或更严谨的 CRC-8/CRC-16。

---

### 2. 正则用 `search()` 而非 `fullmatch()`，会匹配到垃圾数据

**位置**: `hardware_layer.py:214` (`_parse_line` 方法)

**描述**: 三个正则均使用 `re.search()`，只要子串匹配即返回，不要求整行完整。

```python
RE_WEIGHT = re.compile(r"B1,(\d+),B0")

# 噪声中包含恰好匹配的子串 → 被当合法帧
>>> re.search(r"B1,(\d+),B0", "噪声数据B1,9999,B0后面还有乱码")
<re.Match object; span=(4, 14), match='B1,9999,B0'>

# 两帧粘在一起 → 第二帧被丢弃
>>> re.search(r"B1,(\d+),B0", "B1,1500,B0B1,2500,B0")
<re.Match object; span=(0, 10), match='B1,1500,B0'>
```

**建议**: 使用 `fullmatch()` 或加 `^...$` 锚定：

```python
RE_WEIGHT = re.compile(r"^B1,(\d+),B0$")
```

---

### 3. 接收缓冲区无上限，MCU 故障会导致内存泄漏

**位置**: `hardware_layer.py:252-256` (`recv_loop` 方法)

**描述**: 如果 MCU 持续发送数据但从不发送 `\n`（固件 bug、死循环发同一字节等），`_recv_buffer` 无限增长直至 OOM。香橙派内存有限（通常 512MB-1GB），OOM killer 杀掉 Python 进程后整个网关宕机。

```python
self._recv_buffer += data       # ← 没有上限检查

while b"\n" in self._recv_buffer:   # ← 没有 \n 就一直拼
    ...
```

**建议**: 加入上限和超时丢弃：

```python
MAX_BUFFER = 4096
if len(self._recv_buffer) > MAX_BUFFER:
    logger.warning("接收缓冲区溢出 (%d bytes)，清空", len(self._recv_buffer))
    self._recv_buffer = b""
```

---

### 4. 开门指令无 MCU 应答机制

**位置**: `door_flow.py:56-59` (开门) + `door_flow.py:70` (关门)

**描述**: `send_cmd()` 只检查 Python 层"串口写操作是否成功"，不关心 MCU 是否收到并执行了指令。

```python
# 开门：检查了返回值
if not serial.send_cmd(door_index, "open"):
    return None    # ← 只覆盖了 Python 层的错误

# 关门：返回值直接被丢弃
serial.send_cmd(door_index, "close")   # ← 门可能没关！
```

如果 MCU 掉线/死机/指令损坏，香橙派毫不知情，会继续拍照、等待重量、上报事件——整条数据链都是废的。

**建议**: 协议增加应答帧：

```
MCU 收到开门指令 → 回复 D1,{index},ack,D0
MCU 门已打开     → 回复 D1,{index},opened,D0
```

香橙派等待应答（含超时），无应答则中止流程并上报错误。

---

## 中等问题 (4)

### 5. 解析异常被静默吞噬

**位置**: `hardware_layer.py:258-261`

```python
try:
    line = line_bytes.decode("utf-8", errors="replace")
    self._parse_line(line)
except Exception:
    pass          # ← 任何异常都无声消失
```

例如 MCU 发了 `B1,abc,B0`（非数字重量）→ `int("abc")` 抛 `ValueError` → `pass`。生产环境中无法定位问题。

**建议**: 至少记一条 warning，包含原始字节：

```python
except Exception as e:
    logger.warning("协议解析异常: %s | raw=%r", e, line_bytes)
```

---

### 6. 参数范围未校验

**位置**: `hardware_layer.py:216, 225, 234`

```python
flag = int(m.group(1))       # 溢满标志，可以是 999
weight_g = int(m.group(1))   # 重量，可以是 -500 (通过其他手段输入)
flag = int(m.group(1))       # 烟雾标志，同上
```

`\d+` 匹配任意长度数字串，无上下限约束。如果 MCU 固件异常输出超大数据，代码照单全收。

**建议**:

```python
weight_g = int(m.group(1))
if not (0 <= weight_g <= 100000):   # 0 ~ 100kg
    logger.warning("重量值异常: %dg", weight_g)
    return

flag = int(m.group(1))
if flag not in (0, 1):
    logger.warning("标志位异常: %d", flag)
    return
```

---

### 7. `wait_for_weight` 可能读到旧数据

**位置**: `door_flow.py:98-118`

**描述**: `BinState` 存的是 MCU 最近一次上报的原始重量，不区分是否来自"本次开门"。如果开门前 BinState 里已残留 2.5kg 的历史数据，`wait_for_weight` 会立刻读到 `w > 0` 并跳过等待。

**建议**: 开门前先清零该门的重量：

```python
BinState.update_weight(door_index, 0)   # 清零，然后开门
serial.send_cmd(door_index, "open")
```

---

### 8. 超时返回 `0.0` 语义模糊

**位置**: `door_flow.py:117-118`

```python
return last_weight   # 未收到数据时为 0.0
```

调用方 (`delivery_handler.py:62`) 直接把 `result["weight"]` 填入 `notify_delivery_complete`。`weight=0.0` 表示"没投东西"还是"传感器故障"？后端无法区分。

**建议**: 用 `None` 表示"无数据"：

```python
last_weight = None   # 初始化为 None
# ...
if last_weight is None:
    return None       # 门控闭环失败
```

或增加一个 `weight_valid` 标志位。

---

## 轻微问题 / 改进建议 (4)

### 9. `_parse_line` 无返回值

**位置**: `hardware_layer.py:209-238`

所有分支都以 `return` 结束（或 fall-through），外部调用无法知道一行是否被成功解析。协议升级后的新帧类型会被静默忽略——当前阶段可接受（向前兼容），但建议记 debug 日志以便追踪未知帧。

---

### 10. 协议不支持舱门编号（已知 TODO）

**位置**: `hardware_layer.py:217`

```python
BinState.update_overflow(0, flag)  # TODO: 多舱门需MCU协议增加舱门编号
```

当前所有传感器数据硬编码到 0 号门。6 舱门场景下协议必须扩展：

```
旧:  A1,{flag},A0
新:  A1,{door_index},{flag},A0
```

---

### 11. `recv_loop` 轮询效率偏低

**位置**: `hardware_layer.py:262-263`

```python
else:
    time.sleep(0.01)   # 无数据时 10ms 空转
```

串口已配置 `timeout=0.5` 阻塞超时，可以直接用阻塞 read 替代 `in_waiting` 检查：

```python
line = self.serial_port.readline()   # 阻塞直到 \n 或超时
```

---

### 12. 旧 MockSerialBridge 重量注入时机不真实

**位置**: `test_mode.py:81-86`

```python
if cmd == "open":
    weight = random.randint(500, 5000)
    BinState.update_weight(door_index, weight)   # ← 立即注入
```

真实场景下，开门 → 投放 → 几秒后重量稳定 → MCU 上报。模拟立即注入掩盖了 `wait_for_weight` 时序正确性的验证。建议模拟中加入随机延迟（1-5 秒）后再注入。

---

## 建议修复优先级

| 优先级 | 编号 | 问题 | 理由 |
|--------|------|------|------|
| **P0 上线前必修** | #4 | 无 MCU 应答 | 数据可靠性，投递全链路 |
| **P0 上线前必修** | #2 | search() 太宽松 | 可能接受垃圾数据 |
| **P0 上线前必修** | #1 | 无校验和 | 数据完整性 |
| **P1 尽快修复** | #3 | 缓冲区无上限 | 稳定性（OOM 风险） |
| **P1 尽快修复** | #7 | 读旧重量 | 重量数据准确度 |
| **P1 尽快修复** | #8 | 超时返回 0.0 | 语义正确性 |
| **P2 可延后** | #5 #6 #10 #11 #12 | 诊断/效率/多门 | 不影响基本功能 |
