# P1AL：同次清运再次开锁的原子准备与永久账本核对

日期：2026-09-13。范围：本地原生 UART 2.0 候选、实际 MCU C 动态库、Python 3.11、
隔离业务 SQLite 和永久更新器 SQLite。没有连接实机、操作真实串口/GPIO、修改 HMI、
烧录、部署或改真实数据。P3/P4 尚未整体完成。

## 本批行为

清运进行中，MCU 收到本次清运的再次开门请求后，先保留原按钮事件。香橙派精确保存该
事件，再为它准备唯一的开锁命令、动作号和回执号。本批仅处理原 MCU 启动期间同一次清运
的普通再次开锁，即 `recoveryGeneration=0` 且 `cleanActionSequence>0`；不是重启恢复清运。

香橙派将原按钮所属的 MCU 启动编号、开始命令号、业务号、投口、配置和步骤编号一起核对。
第一次解锁的原永久许可和完整正常通断电证据必须已经核对完成，原清运占用必须仍在。
首次确认仅因回复丢失而待处理时，可由既有待办核对自动补齐，然后继续，不新增人工操作。

- 同一次按钮重复观察、数据库重开、准备后进程退出，均找回原命令和回执，不换号。
- 命令、动作关联和命令序列在同一业务库事务提交；中途退出不会留下半条动作或消耗第二个号。
  两个数据库连接并发准备同一按钮，也只生成一份。
- 不允许将完成按钮解释成开锁，也不允许给同一按钮任意更换逻辑动作键来再开一次。
- 再次开锁沿用首次脉冲时长，不能扩展原操作窗口；重复读取不会改写已冻结命令的剩余时长。
  实际发送仍由调用方提供的原截止时刻和当前安全检查约束，MCU 保持原清运整体期限。
  此准备接口本身不发送串口，也不是实时接单许可。
- 一次发送后仅查询原命令和原输出。受理回复丢失、香橙派重启、永久账本回复丢失均不重发开锁。

实际 C 产生通电和断电记录后，香橙派沿精确交接保存原文。每次再次开锁的证据包包含
原开始命令、原受理、首重、首次解锁已确认摘要、本次按钮原文及保存回执、本次独立命令
和两条实际输出及保存回执。核对通过只确认该动作，不结束清运、不释放业务许可或占用，
不创建清运完成、余额、提现、新袋或新重量基准。

例如再次开锁只通电 100 毫秒就因更新停止而断电：保留两条实际记录，但不把它确认为正常
脉冲完成。原动作仍待核对，业务继续占用；不能把短脉冲改成“从未执行”或“清运门已关闭”。
同样，正常脉冲只证明输出，不证明实际门位、锁线圈健康或清运员已经关门。

## 重启、冲突与最终重量

业务库和永久账本库仍是两个独立事务系统。先提交不可变待确认证据，再调用永久确认；
重启后先查询原永久回执和摘要，匹配才确认本地。请求丢失只重试原确认，回复丢失只补核对。
不会重新登记机械授权或调用串口。跨库原子性限制沿用 P1AK，不隐瞒晚到冲突。

首次输出或本次按钮出现矛盾原文时，保留冲突并阻止新的准备/正常确认；已有历史证明不覆盖。
缺少按钮精确保存关系时，旧 Python 内存对象不能替代落盘证据。

实际 C/Pi/SQLite 纵向测试覆盖：首重 500 克 → 第一次完成意图和末重 123 克 → 再次开锁 →
第二次完成意图和新末重 456 克 → 本步骤清运员关门确认 → 最终结果精确保存。
最终结果采用 456 克及其测量身份和第 3 步，旧 123 克原文仍可查；不复用旧完成按钮或旧末重。
测试结束仅产生一条 `PENDING_CLASSIFICATION`（等待结果分类）任务，原业务和永久许可仍活动。
这不是已接上云端清运应用，也不替代 HMI 完成/返回按钮的交互确认。

## 文件和兼容性

- `hardware/mcu_action_evidence.py`：普通再次开锁的稳定动作键、首次确认和精确按钮关联、
  独立证明格式 `ecobin-native-clean-reopen-effect-v1`；首次投递/清运的证明格式与摘要不变。
- `hardware/edge_store.py`：原准备和绑定逻辑提取为事务内方法，新增
  `prepare_native_clean_reopen` 将命令/动作/回执同事务提交；原公开准备接口仍独立管理事务。
- `hardware/tests/test_native_clean_action_reconciliation.py`：29 项新增专项。
  复用实际 C 执行场景，只注入串口、时钟和硬件边界，不手造正向输出。
- `hardware/tests/test_mcu_delivery_execution.py`：联合场景额外公开其实际 C 执行上下文及当前测试时间。

候选 schema **28**、UART **2.0.0-rc.20 / CLEAN_INTERRUPTION_CUSTODY_NOT_RUNNABLE** 不变。
无新数据库表、UART/OneNet 消息或永久 RPC。本批没有修改 MCU C，也没有重新构建可烧录固件。
P1AJ 的 ROM51648/RAM6096 仍只是历史核心探针。新准备器/核对器未接 `main.py`/正式业务循环，
运行清单的 schema25 冻结以及新模块尚未纳入正式打包的边界仍保留，不能直接部署候选源码。

## 验证

按 TDD 技能逐行为先失败再实现：普通再次开锁此前被拒绝；任意动作键此前可通过；首次确认
缺失和原按钮未保存此前不阻止绑定；原子准备接口此前不存在。分别观察失败后实现并通过。

- 新专项最终 **29 通过，8.01 秒**。
- 提取事务内方法后原接口/新行为组合 **102 通过，25.32 秒**。
- 最终新专项、P1AK 专项、实际首次执行和原命令会话组合 **137 通过，31.83 秒**，退出码0。
- 五个真实子进程 `os._exit(77)` 断点：命令插入前/后、关联插入前/后、事务提交后；重开库验证
  原命令序列及单份动作。不是 TF 卡物理断电证明。
- 覆盖两库重启、请求/回复丢失、并发准备、禁止窗口扩展、按钮/首次输出冲突、真实短脉冲，
  以及重新称重后完整清运结果交接。缺值、冲突和待核对状态不被伪装成正常完成。
- 完整契约校验 **25 通过 / 0 notes**：104 生成物、67 消息、50 黄金帧、1707 流轨迹、16 摘要；
  Python/Java21/C 独立头文件与共享校验实现一致。本批未改契约，验证确认无漂移。
- 四个本批 Python 文件按 3.11 语法解析通过；相关 `git diff --check` 通过，仅有既有 CRLF 提示。
- 扩大回归 **1831 通过 / 1 失败 / 5 跳过 / 1636 子测试通过，336.73 秒**，退出码1，不是全绿。
  五项跳过为 Windows 缺 root 独立业务身份、POSIX flock、Unix 对端认证和两项 POSIX 权限位。

扩大回归唯一失败仍为既有 `test_committed_edge_state_survives_forced_process_termination`：
强杀后打开库，在 `PRAGMA journal_mode=WAL` 报 **1546 / SQLITE_IOERR_TRUNCATE**，发生于迁移之前。
按 diagnose 技能核对本轮原始诊断清单与副本大小/摘要，没有打开、恢复或修改故障数据库副本。
本批未针对该既有问题加入生产重试、睡眠、跳过或降低 WAL/FULL 设置，仍未解决。
历史独立重复通过不能推翻此次失败；本次不重复旧的低复现率循环来声称修复。

错误后的顺序留证在 pytest 自动保留目录之外，但仍是可被系统清理的临时诊断目录，不是永久
审计存储或错误发生前的原子快照：

```text
C:\Users\24217\AppData\Local\Temp\ecobin-sqlite-failure-evidence\81283861b107406a942e114c56ad00cd
edge.db       4096 bytes   SHA256 5c9dec1886cc01f5f2307ee1ea87f9b32a4cef0f8f9a2c68beebc558013bedab
edge.db-wal 613912 bytes   SHA256 2c7787ab40dfb272f4aa1bd4c2bb27c85fe2b48ae55d55e56f8113b1bc0b5c54
edge.db-shm  32768 bytes   SHA256 8527cce09d93b92aa5ed93ae8b0b949a87ca0ebd941398472f7f8dbcee3e7131
```

复跑命令（沿用缓存工具环境，不重新下载依赖、不重建 hardware/.venv）：

```powershell
$py = 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe'
& $py -m pytest hardware/tests/test_native_clean_action_reconciliation.py hardware/tests/test_native_action_reconciliation.py hardware/tests/test_mcu_delivery_execution.py hardware/tests/test_native_command_session.py -q --tb=short
$env:JAVA_HOME = 'C:/D/002-Tools/004-DevTool/jdk-21.0.10'
$env:PATH = "$env:JAVA_HOME/bin;$env:PATH"
& $py contracts/tools/validate_contracts.py
$tests = @(rg --files hardware/tests | Where-Object {
    $_ -match '[\\/]test_(mcu_|native_|uart2|edge_store|job_safety|work_manager|updater_store|edge_boot|process_kill_recovery|business_runtime_cutover|stage4).*\.py$'
})
& $py -m pytest contracts/tests @tests -q -rs --tb=short
```

## 下一步

继续异常/未知效果的独立持久证据、受限新恢复关门许可及真实反馈；不先解除旧动作阻断来
绕过永久账本，不把旧动作补记成功。随后接业务结果分类和主循环。恢复清运代次、真实 RS485
迟到回复归属、全部配置消费者、HMI/热点验收、后端恢复事务和成对打包/真机联调仍未完成。
迟到完整结果与问题归档的资金取舍仍按计划在对应阶段询问负责人，不擅自裁定。
当前无需用户操作；整体实施目标保持进行中。
