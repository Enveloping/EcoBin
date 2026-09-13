# 原生 UART 2 原业务查询 P1D

日期：2026-09-12。承接 [P1C 完整结果交接](uart2-result-handoff-p1c-2026-09-12.md)。
本批为本地候选实现，不是设备上线：未 SSH、部署、烧录、刷卡、操作 GPIO、修改 HMI 或云端。

## 本批能证明什么

香橙派在原业务没有得到明确进展时，可以使用原 START/RESUME 的启动编号、命令序号、
命令 UUID/摘要及业务 UUID/类型/投口查询。MCU 返回同一份业务记录的状态、阶段及结果引用。
所有回复完整回显查询身份，不能用另一笔业务或上次查询的回复解释当前业务。

| MCU 回复 | 事实及香橙派本批行为 | 对业务/资金的影响 |
|---|---|---|
| RUNNING（处理中） | 返回原业务阶段，香橙派继续观察 | 不重发开门，不清占用 |
| RESULT_HELD（保留结果） | 返回结果编号/摘要，可据此核对完整结果 | 引用不是完整结果，不提前确认保存 |
| RESULT_RELEASED（结果已交接） | MCU 已收到精确保存确认，保留最近结果引用 | 不代表云端已完成或设备已可接单 |
| NOT_FOUND（详情未保留） | 需要结合香橙派已存结果和启动编号继续判断 | 不证明业务没发生，不许可重发动作 |
| IDENTITY_CONFLICT（身份冲突） | 相同业务/命令身份的其他字段不一致 | 不覆盖原业务，不据此恢复接单 |
| BOOT_MISMATCH（启动不符） | 当前 MCU 编号不同，包括尚未绑定的 0 | 不作废香橙派已经保存的完整结果 |
| 没回复、过期回复或同次查询回复冲突 | 香橙派保持“未知”，后续用新查询编号继续观察 | 不冒充 NOT_FOUND，不释放原占用 |

不可取得详情时，回复中的 phase=IDLE 只是**本次所查业务的空槽编码**，不是整机空闲。
本批业务观察器不修改既有工作占用，不建单、不换袋、不增加余额、不触发提现或人工隔离收口。

## 实现及边界

- Registry 为 `2.0.0-rc.4` / `WORK_QUERY_NOT_RUNNABLE`，53 条消息。新增 QUERY_WORK（86 字节正文）
  和 WORK_QUERY_REPLY（132 字节正文）；完整帧分别为 100、146 字节，无通用帧 ACK。
  字段/枚举/布局仍由 contracts 单一生成，Python/C/Java 校验累计覆盖 14 条新引导/会话消息。
- `mcu_work_state.c/.h` 组合原业务身份、阶段及上一批结果槽，只保留最近一笔业务。
  接受业务记录、更新阶段、冻结结果、精确保存确认、读取回复在同一前台所有者内完成。
  新业务仅能替换已交接的记录；重复或更旧启动记录不能重启本模块中的旧业务。
  已退役更早详情仍可能查不到，完整拒旧动作规则继续由 McuSession 高水位负责。
- `BeginAccepted` 不是命令执行器：调用者必须先完成 START/RESUME 的完整校验与真实受理。
  本模块不执行 GPIO，也不检查完整业务阶段转移表。`Complete` 核对结果所属原业务/原命令后，
  才让结果槽完整验证并冻结正文。实际结果组装、测量及业务状态机接线仍待完成。
- `EdgeStore.reserve_native_query_id()` 使用现有 `device_state` 表，schema 保持 19；
  BEGIN IMMEDIATE + COMMIT 后才返回编号，最大为 2^53−1，不回绕，允许预留后未发送留下空洞。
  计数损坏/耗尽、提交失败均拒绝发送；两个连接并发预留不重号。禁止嵌套且不会回滚外层事务。
  这不是 MCU 启动编号或动作序号分配器；不能通过恢复过期数据库、克隆设备身份或清库维持此保证。
- `mcu_work_query.py` 是单前台所有者的只读观察器，外部注入独占串口写入函数。
  每次编号提交后只调用一次 write；短写不补写，OSError 不原样重发，下次查询使用新编号。
  只接受当前查询窗口内完整身份匹配的回复；相同回复可去重，异文冲突变回未知。
  默认查询间隔 1000 ms，可注入，属于候选工程参数，并非新增已冻结业务等待期限。
- 写入函数还必须保证底层不缓存重发/复制请求、具有有界超时。当前旧 UartLink 并未因此改变。
  观察器和 MCU 状态模块均未接 main/Keil/真实 UART；无自动探测、双解析或协议回退。

## 验证证据

采用先测试后实现：先复现不存在查询消息/模块，再实现；共享非法轨迹先在 Java 实际失败
（work_query_invalid_0），补 C/Java 关系校验后跨语言全部通过。

- 查询合法状态、投递/清运全部业务阶段、错误类型/启动/阶段/结果引用及 CRC 正确的非法内容。
- C 主机行为测试检查每次查询前后内存不变；重复完成、错误确认、旧确认、新业务替换、MCU 重启。
- Python 直接调用 Clang 编译的实际 C 动态库，分别跑投递与清运：
  Python 查询 → C 返回处理中 → C 冻结完整结果 → Python 查询并读取 → 真实 SQLite 原子保存 →
  关闭并重建 Pi 数据库连接/查询器 → C 仍保留结果 → 同一结果去重保存 → 精确确认 → C 已交接。
  随后 C 重启归零返回启动不符，SQLite 已存完整结果仍保留。测试不使用 Python 模型代替 MCU 状态。
- 真实 SQLite 提交前可见性、COMMIT 拒绝、子进程 `os._exit` 后编号延续、双连接并发、嵌套事务、
  损坏/耗尽计数、短写/异常、迟到回复、同查询异文冲突。无真实串口或机械操作。
- ARMCC 5 / Cortex-M3 / C99 / O2 独立编译无错误/警告，RAM 静态上限 304 字节（包含结果槽）。
  独立对象 Code（含内嵌数据）5172 字节、RO Data 286 字节；不是整机 ROM 或栈预算。
  上批结果校验已存在 408 字节所见调用链，本批新增调用层，**仍不能认为原 512 字节栈足够**。
- 契约验证：24 项通过、0 notes；103 个生成文件一致，29 个帧、304 条流轨迹、4 个摘要向量，
  Python 3.11 / Java 21 / C11 全部通过。
- 最终定向回归：322 passed、31 skipped、1327 subtests passed（48.53 秒）。
  Windows 跳过的 POSIX 权限/锁/链接及安装测试不是 Linux 或真机验收。
  `git diff --check` 通过，仅既有 CRLF 转换提示；旧运行入口/三个冻结制品及 OneNet 未改。

## 发布限制与下一批

安装清单包含独立候选模块，避免将来漏文件；正常运行入口未启用它们。冻结的三个旧 UART 运行
制品不变，`--include-hardware-mcu` 仍禁止。既有用户修改和 `hardware_mcu.zip` 保留。

另核实了后端 `BusinessReleasePackageVerifier`：仍只接受原固定文件集合与 schema 18，
会拒绝带 `uart2_protocol.py` / `mcu_work_query.py` 的 schema 19 候选包。本批**没有放宽它**。
正式成对发布前必须同步版本化文件清单、保留数据迁移/回退及设备侧预检；不能把当前目录直接打包远程推送。

下一批优先补“同一时刻的设备事实快照”：门最近有效目标、实际输出、防夹、测量/传感器及配置事实。
本次 QUERY_WORK 只保证所查业务记录一致，不代表以上物理/配置事实已经采集或足够决定接单。
之后继续实际 Pi 启动/动作编号与单次发送、MCU 业务状态机/测量/结果组装、Pi 分类恢复和
OneNet/后端同步，并补整机链接/栈/时序验证。HMI 和归档后迟到完整结果的资金规则仍待确认。
P0/P1/P3 整体没有关闭；当前不需要用户烧录、修改 HMI 或开启远程维护。

复现命令（Java 21、Clang 在 PATH）：

```powershell
& 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe' contracts/tools/validate_contracts.py
& 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe' -m pytest contracts/tests hardware/tests/test_mcu_work_query.py hardware/tests/test_mcu_work_state_c.py hardware/tests/test_native_result_handoff.py hardware/tests/test_mcu_result_slot_c.py hardware/tests/test_edge_store.py hardware/tests/test_mcu_session_c.py hardware/tests/test_mcu_control_modules_c.py hardware/tests/test_process_kill_recovery.py hardware/tests/test_runtime_release_install.py hardware/tests/test_business_runtime_cutover.py hardware/tests/test_image_software_installer.py -q --tb=short
```
