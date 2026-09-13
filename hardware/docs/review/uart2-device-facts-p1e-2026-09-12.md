# 原生 UART 2 单投口设备事实 P1E

日期：2026-09-12。承接 [P1D 原业务查询](uart2-work-query-p1d-2026-09-12.md)。
本批为本地候选实现：未 SSH、部署、烧录、刷卡、操作真实 GPIO、修改 HMI、物模型或云端。
旧设备继续使用原协议，不能把本批编译产物当作新协议已可上线。

## 能证明什么

香橙派可以按目标 MCU 启动编号和投口发起一次只读查询，MCU 回显同一查询身份，返回抓取时
已知的门控、配置、原始读数、最后一轮测量和最近保留业务摘要。香橙派收到后只更新本地观察器，
不改业务占用、不确认结果、不让门动作、不建单、不增加余额或触发提现。

| 场景 | 返回的事实与处理 |
|---|---|
| 关门时 PB5 刚触发，但控制中断尚未处理 | 返回上一次控制刷新的 PB5 和施加输出，以及那次刷新时间；不把新输入拼接到旧输出上 |
| 下一次控制刷新已暂停关门 | 保留关门目标，PB5 为触发、暂停为真、PB6/PB7 都停；解除后按既有规则自动继续 |
| 上次读取 500 克，后来读超时或报文损坏 | 当前读取状态变为失败，重量槽清零作为缺值编码，不复用 500 克，也不解释为测得 0 克 |
| 原始读数仍是有效格式，但已过期 | 保留原采集时间供排查；香橙派不再把它当作当前重量 |
| 测量完成后又应用新配置 | 最后一轮测量仍带原配置版本与原结果时间，当前应用配置另列，不追溯改写旧测量 |
| MCU 已重启或所查投口不支持 | 明确返回启动不符/投口不支持；事实正文清零表示不可用，不表示门关、重量为零或整机空闲 |

这里的“一致”是**同一次门控刷新、抓取时已知的事实集合**，不是所有传感器在同一瞬间采样。
PB6/PB7 表示最近施加给 GPIO 的输出命令，不是独立电气反馈；门业务状态仍按最近有效方向，
不声称测得物理门位。清运锁通电/断电也不能推导清运门开关。

## 契约及实现

- 唯一 Registry 为 `2.0.0-rc.5` / `DEVICE_FACTS_NOT_RUNNABLE`，共 55 条消息。
  新增 QUERY_DEVICE_FACTS（17 字节正文）和 DEVICE_FACTS_REPLY（204 字节正文、218 字节总帧），
  单帧不分片、无通用帧 ACK。16 条新引导/会话消息由 Python/C/Java 校验同一组合法与非法内容。
  两条查询不替换或启用旧 QUERY_STATE/STATE_SNAPSHOT 多包运行入口，不增加自动探测、双解析或回退。
- `actuator_runtime` 在一次控制刷新中缓存 PB5、门输出和刷新时间；Snapshot 在既有临界区内
  复制控制事实与抓取时间，不读取新 PB5 冒充当次控制依据，也不为了查询写 GPIO。
  该模块此前已经接入旧主程序，本批修改已完整重编译；不是只有独立候选代码变化。
- `runtime_clock` 保留原 32 位接口，新增高字累计和 64 位读取，避免约 49.7 天回绕后旧样本显新。
  宽时钟在 Cortex-M3 上不是原子读：调用者必须屏蔽时钟中断，或处于该中断的 Advance 之后；
  禁止在能抢占时钟更新的高优先级中断直接读取。Advance 单写者、Init 只在 MCU 真正启动执行。
- `mcu_device_facts.c/.h` 是尚未接主程序/Keil 的前台发布缓存。配置、称重和业务状态必须由
  同一个前台所有者发布/读取，不允许 ISR 同时改缓存。编码及完整校验在恢复原中断状态后执行，
  不把整个 204 字节编码过程放进新增的中断屏蔽区。
- 配置发布入口只接受实际应用后的版本与两种摘要，拒绝回退/同版本异文；它不执行完整配置事务。
  原始读取调用实际 ScaleReader 验证完整 Modbus 字节，区分有效、超时、CRC、量程和协议错误。
  尝试编号必须递增、不回绕；采集时间不能倒退或晚于当前 MCU 时间。未来真实 RS485 接入者仍须
  先确认请求归属和完整帧到达时间，不能用迟到帧或主循环处理时间伪造新样本。
- 最后测量发布入口接实际 WeightMeasurementResult；保留测量编号、观察时间、所用配置版本、
  耗时、样本数、结果及跨度。观察时间是读取测量核心结果的时间，不是新的原始采集时间。
  同编号终态不可改写，重复发布也不刷新时间；RUNNING 的 elapsed=0 仅表示没有最终耗时。
  此记录不是业务前后重量的替代品，更不能用来确认完整结果已保存。
- 保留业务摘要组合实际 McuWorkState：进行中、保留结果、结果已交接分别表达；查询不清结果。
  摘要没有完整命令身份/结果摘要，不能替代 QUERY_WORK、WORK_RESULT 和精确 RESULT_SAVED。
- `McuDeviceFactsQuery` 与原 McuWorkQuery 共用只读发送骨架：真实 SQLite 查询编号提交后，
  对注入的串口写函数只调用一次；短写/异常不补写，后续尝试使用新编号。只收当前期限内精确
  回显身份的回复，迟到/其他身份忽略，同次异文冲突变回未知。没有打开实际串口。
- 香橙派判原始读数年龄的保守上界为：MCU 抓取时距采样的时间，加香橙派自查询发起后的时间。
  不直接相减两端独立时钟。默认读数年龄上限 750 ms、查询间隔 1000 ms 是可注入的候选工程参数，
  不是新业务超时规则；读数变旧后仍可查看诊断字段。有效原始数值不等于稳定测量或接单许可。

## 验证证据

按 `tdd` 技能先写行为测试，再实现：先暴露 PB5 与旧输出混合、不存在的新消息/宽时钟接口和
非法内容缺少校验，再逐步补实现。测试使用实际 C 核心，仅 GPIO/时钟和串口写边界由测试控制，
不以 Python 状态模型代替 MCU 事实采集。

- 同一次门控刷新、开门忽略 PB5、更新停止保留目标；查询不写输出、不改变事实/业务缓存。
- 实际 Modbus 500 克与真实 0 克、超时/CRC/超量程/缺字节、重复/未来采集时间拒绝，以及 32 位回绕。
- 实际称重核心以 250 ms 输入 20 个 0/500 克交替样本：5 秒结束，得到 250 克中位数、500 克跨度；
  发布后配置更新不改写原测量，重复终态不刷新时间，冲突内容拒绝。
- Python 查询 → 实际 C 抓取 → 生成解码器 → SQLite 查询编号；检查传输耗时计入读数年龄、
  过期/迟到回复与启动归零、无结果上报任务产生。
- 实际业务记录从进行中到结果保留，再到精确交接；查询不释放保留的原始完整结果。
- 218 字节回复的全部 217 个拆包位置，以及共享有效/非法内容和流式恢复轨迹。
- 契约工具：24 passed、0 notes；103 个生成文件一致；31 个帧、384 条流轨迹、4 个摘要向量，
  实际 Python 3.11 / Java 21 / C11 三端通过。
- 扩展回归：431 passed、31 skipped、1616 subtests passed（58.85 秒）。之后补强的查询不改缓存、
  超量程/协议错误和重复终态时间断言，P1E 定向 9 项再次通过（5.15 秒）。
  Windows 跳过的 POSIX 权限/锁/链接及安装测试不是 Linux 或真机验收。
- ARMCC 5 / Cortex-M3 / C99 / O2：facts、actuator、clock 三模块独立编译无警告/错误。
  facts 缓存静态上限 192 字节（不含 304 字节业务槽预算和外部帧缓冲）；独立对象 Code 含内嵌
  数据 7212 字节、RO Data 294 字节，不代表最终链接增量。
- Keil USART1-DEMO 全量重编译 0 错误/0 警告：Code 15784、RO 304、RW 136、ZI 1144 字节，
  map 总 ROM 16224、总 RW 1280 字节。调用图可见最大栈 208 字节，仍标记未知间接调用/环路。
  它只包含现有已链接模块，尚未包含新会话/结果/业务/facts；前批结果调用链已到 408 字节，
  **整套新候选的原 512 字节栈仍未证明足够**，必须在接入前完成全栈/中断/时序预算。
- 生成检查及 `git diff --check` 通过（仅 CRLF 转换提示）；旧三个冻结制品摘要及 OneNet 不变。

## 尚未完成与续作

本批只含一个受支持投口及公共配置/最近业务事实；没有烟雾、红外或完整传感器健康集合，
不替代全部安全状态和接单检查。不要用 AVAILABLE、清运锁断电、逻辑 CLOSE 或 PB5 状态
自行解锁旧业务。现有门控安全规则不因增加查询而放宽。

下一批先补实际香橙派启动编号、动作序号的持久预留和单次发送/原命令查询，与已有 MCU 会话核心
交叉验证进程退出和丢回复。之后接配置事务与真实 RS485 采集入口、MCU 业务状态机/结果组装、
完整安全事实、Pi 业务分类和恢复，再同步 OneNet/后端及验收。HMI 返回交互与归档后迟到完整结果
的资金规则仍未确认，不自动采用提案。

schema 保持 19，数据保留；后端仍只接受原文件集合与 schema 18，未为了候选放宽发布检查。
新候选模块进入文件清单不代表启用；仍不得将当前目录直接打包远程推送。真实串口继续由业务
进程独占，永久更新器的动作许可与账本边界不变。原有用户修改与 `hardware_mcu.zip` 保留。
本批不需要用户烧录、修改 HMI 或开启远程维护；P0/P1/P3 整体没有关闭。

复现主要命令（Java 21、Clang 在 PATH，Python 使用实际 3.11 环境）：

```powershell
python contracts/tools/generate_contracts.py --check
python contracts/tools/validate_contracts.py
python -m pytest contracts/tests hardware/tests/test_mcu_device_facts.py hardware/tests/test_mcu_work_query.py hardware/tests/test_mcu_work_state_c.py hardware/tests/test_native_result_handoff.py hardware/tests/test_mcu_result_slot_c.py hardware/tests/test_edge_store.py hardware/tests/test_mcu_session_c.py hardware/tests/test_mcu_control_modules_c.py hardware/tests/test_process_kill_recovery.py hardware/tests/test_runtime_release_install.py hardware/tests/test_business_runtime_cutover.py hardware/tests/test_image_software_installer.py hardware/tests/test_mcu_runtime_logic_c.py hardware/tests/test_mcu_update_execution_c.py hardware/tests/test_mcu_delivery_completion_source.py hardware/tests/test_fixed_frame_mcu_adapter.py hardware/tests/test_fixed_frame_mcu_maintenance.py hardware/tests/test_uart_protocol.py hardware/tests/test_uart_link_v1.py -q --tb=short
```
