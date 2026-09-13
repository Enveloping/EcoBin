# 原生 UART 2.0 身份契约切片 P1A

日期：2026-09-12。用户已授权继续本地实施；本批不部署、不烧录、不连设备、不修改 HMI。
P1A 是本地实施切片标签，不是正式任务编号。依据为
[分批计划](../../../docs/planning/mcu-edge-contract-implementation-plan-2026-09-12.md)及
[P0 条件性模型](mcu-session-p0-model-2026-09-12.md)。

## 1. 实际完成与运行边界

唯一可变机器来源 `contracts/uart/uart-registry.yaml` 从 `1.0.0-rc.3` 推进到
原生 `2.0.0-rc.1`。版本不是旧固定帧协议名称中的 v2。
实施阶段明确为 `IDENTITY_ONLY_NOT_RUNNABLE`：完成身份候选，不是可运行固件协议。
原 39 条消息保留迁移位置，增加 4 条引导消息；完整结果、状态、门事实、称重和恢复
语义仍未全部迁移。不能把候选保留的旧字段当成最终新规则。

旧运行程序仍导入 `hardware/uart_protocol.py`；固定帧链路也经 `command_processor`
间接依赖其配置摘要函数。因此直接覆盖该模块会让旧运行程序套用新版本字段。
本批采用一个可变 Registry + 冻结旧制品：

- `hardware/uart_protocol.py` 和 `hardware_mcu/USER/uar/` 的头文件、黄金程序均原样保留。
- `contracts/uart/frozen-v1-runtime.json` 锁定 3 个旧制品的 LF 文本 SHA-256 和旧来源摘要；
  每次生成/漂移检查先验证，变动即失败。不能更新清单摘要来绕过。
- 新 UART 仅生成到 `contracts/uart/generated/` 和契约样例/目录，不加入设备发布清单。
- `--include-hardware-mcu` 明确拒绝，包括检查模式；待双方实现和成对发布审查后再解除。
- OneNet 生成流程不变，本批未修改物模型或后端。现有运行模式仍仅 `fixed-frame` / `uart-v1`，
  不新增自动探测、同端口双解析或失败回退。

## 2. 新的消息内容

所有编号为 big-endian 整数；下表为当前机器来源的摘要，完整偏移见生成的
[`uart-layout.json`](../../../contracts/uart/generated/uart-layout.json)。

| 消息 | 方向及用途 | payload / 整帧字节 |
|---|---|---:|
| `BOOT_PROBE` 0x07 | Pi 发全新、持久消耗的询问编号；无机械动作 | 8 / 22 |
| `BOOT_PROBE_REPLY` 0x08 | MCU 回显询问编号和当前启动编号；此处允许未绑定零值 | 16 / 30 |
| `BIND_BOOT` 0x09 | Pi 发原询问编号和已持久分配的非零启动编号 | 16 / 30 |
| `BIND_BOOT_REPLY` 0x0A | MCU 回显请求、当前启动编号及绑定/拒绝状态 | 25 / 39 |

绑定成功必须 `当前编号=拟分配编号>0`；询问不匹配时仍为零；已绑定拒绝时已有非零编号。
普通 HELLO 和关键业务事件仍必须非零，不把所有启动编号校验放宽为零。

15 条命令的公共前缀由 48 增至 60 字节：UUID(16)、内容摘要(32)、目标启动编号(8)、
该启动下的持久命令序号(4)。目标编号/命令序号都必须非零，并纳入命令摘要输入。
命令摘要域升级为 `ECOBIN:UART:COMMAND:v2\0`；配置/快照摘要算法版本暂保持原布局版本，
配置语义摘要明确排除新增传输身份字段。配置/快照后续修改必须继续同步摘要。

引导请求和命令最多发送一次，覆盖通用帧的 3 次上限；绑定回复丢失时使用新询问，
机械命令超时查询原命令。序号持久递增、不回绕；只读查询不推进动作高水位。
这些是候选机器规则，**实际发送器仍未改为此策略**，不据此放行自动恢复。

## 3. 编解码与长度验证

- 4 条引导消息在参考 Python、生成 Python、Java、C 的帧接收路径校验长度、编号范围、
  枚举及绑定回复关系；非法内容不交给流解析回调。CRC 先校验，不能仅靠 CRC 拦坏数据。
- 测试包含截短、多字节、零询问/拟分配编号、超过 JSON 安全整数上限、uint64 全一、
  绑定编号矛盾、未知状态；拒绝后仍可接收后面的合法帧。
- Python 编码 API 允许状态使用符号或数字，两者语义相同。复核发现数字枚举绕过后，
  先加失败用例，再规范化状态值修复；三个状态均有正负例。
- 所有当前登记的 43 条消息均未超过 242 字节 payload / 256 字节整帧。
  最长仍是 `STATE_SNAPSHOT_BEGIN`：229 / 243 字节，剩 13 字节。
  [机器预算](../../../contracts/uart/generated/message-budget.json)和
  [消息目录](../../../contracts/generated/contract-catalog.md)由 Registry 生成。
- 以上不是完整结果集合/分段预算，也不是 MCU RAM、栈或实时性预算。
- C/Java 的其余 39 条消息仍缺完整内容执行器；不能称“三语言全部字段等价”。

## 4. 验证证据

复用缓存解释器 `C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe`，未下载依赖。
新增测试先失败再实现，覆盖运行冻结、引导线格式、CRC 正确的非法内容、身份字段和摘要。

```powershell
python -m pytest contracts/tests -q
python contracts/tools/generate_contracts.py --check
python contracts/tools/validate_contracts.py
python -m pytest hardware/tests/test_mcu_control_modules_c.py hardware/tests/test_mcu_runtime_logic_c.py hardware/tests/test_mcu_update_execution_c.py hardware/tests/test_mcu_delivery_completion_source.py hardware/tests/test_fixed_frame_mcu_adapter.py hardware/tests/test_fixed_frame_mcu_maintenance.py hardware/tests/test_uart_protocol.py hardware/tests/test_uart_link_v1.py -q
git diff --check
```

最终记录：契约 103 项、设备侧定向回归 104 项通过；102 个生成文件无漂移；
24 项契约校验通过、0 notes。Python 3.11 / Java 21 / 通用 C11 同一组
19 个帧样本、53 条流轨迹及 3 份摘要样本通过。与上一批不同，本批实际修改了机器契约
和生成器，但没有修改设备运行模块，也没有重新构建/烧录 Keil 固件。

## 5. 下一步与未关闭条件

下一切片继续 P0/P1：命令受理/拒绝、按原启动与原命令查询、一致状态快照、不可变结果
分段及 SQLite 保存确认，连同完整消息/资源预算。之后才连接 Pi 真实持久计数器、
单次发送和 MCU RAM 绑定/命令防重状态机。

本批线格式检查不验证真实 MCU 正在等待哪个询问，不证明请求在真实 UART 中不会被复制。
P0 模型的通道假设、Pi 数据库不回退前提、完整结果冻结和重启恢复均未被本批替代。
HMI 返回动作、异常归档后迟到完整结果的资金取舍仍待确认，不自行实施。
