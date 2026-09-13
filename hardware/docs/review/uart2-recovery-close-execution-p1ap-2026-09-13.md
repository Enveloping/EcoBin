# P1AP：单片机独立恢复关门执行与原始反馈

日期：2026-09-13。承接 [P1AO 本地恢复意图](uart2-work-recovery-intent-p1ao-2026-09-13.md)
和 [P1AM 受限关门许可](uart2-recovery-close-permit-p1am-2026-09-13.md)。
本批推进 P4 的实际执行端，整体恢复、分类和主程序接线仍未完成；不是上线或真机验收记录。

## 实现了什么

新增 `McuSafeCloseExecution`，在启动时显式挂到原有准备协调器上。候选默认不启用，必须由
实际应用提供明确的 `SAFE_CLOSE` 前置检查。它只处理本控制入口所代表投口的独立投递门关门，
不负责清运门、不推断物理门位、不释放香橙派的原业务或永久动作账本。

目前接通的是 **MCU 重启后已无活动作业的单投口恢复关门**，不是运行中作业的强制抢占，
也不是多个投口的全机关门。运行中的投递/清运、尚保留的结果或动作、配置暂存及称重活动
继续阻止该入口；全机关门范围和其他投口明确拒绝，不假装已执行全部门。
这些限制不替代完整计划，之后需要按原业务中断事实和多投口能力继续接入。

收到新的合法关门指令后，MCU 依次核对当前启动/命令身份、应用前置条件、原工作及执行器
是否空闲、范围/投口和剩余执行时间，再预留反馈记录及事件编号额度。成功受理的决定先
缓存，再进入实际控制层；同身份重传只返回原决定，不能重启动作或延长期限。

- 新的关门目标先进入100毫秒双输出关闭期，然后由定时器锁存 CLOSE。CLOSE 沿原代码为
  PB6/PB7=`0/1`，不是实测门位；定时器不等待前台轮询或香橙派保存确认。
- PB5 触发时停止关门输出，但保留关门目标；解除后原动作自动继续，不要求新命令或按钮。
- 已有有效 CLOSE 时，新有效指令可合并到原关门方向，不先断开再重复换向；反馈明确为
  `COALESCED_WITH_EXISTING_CLOSE`，而非再次完成物理行程。
- 未受理前的错误明确拒绝；受理后才遇到执行超时或更新停机，保留原 ACCEPTED 和真实
  `OUTPUT_REJECTED` 反馈，不把受理改写成执行成功。更新停机发生在已下发 CLOSE 之后时，
  保留当时已下发的历史，当前更新锁和输出停止另由设备事实表达。
- 控制层保持原互斥：恢复关门尚在保管时，不能开始新投递循环、清运脉冲或旧固定帧动作；
  也不能把恢复关门用于中止或释放正在保留的清运锁动作。

执行反馈使用现有 `SAFE_CLOSE_RESULT`，保留本次新命令号、启动号、实际控制时刻、投口、
关门输出结论和不可观测门位标记。经原动作记录池、串口查询、香橙派 SQLite 原文提交及
精确保存回执交接后，才释放本次控制记录。释放记录不会把 CLOSE 停掉，也不代表恢复接单。
前台没有及时运行时，控制层保留终态与时刻，不把之后查询时间当成执行时间。

香橙派重启、反馈保存失败、保存请求或回复丢失，只能重新查询原记录；不能再次发关门。
没有精确保存确认时，本准备协调器保持恢复占用并拒绝新的 START 或配置应用。
MCU 实际重启清除旧运行内存；新启动不能接受旧启动号的关门指令，也不会自动续跑旧关门。

## 接线与验证范围

新增 `hardware_mcu/USER/mcu_safe_close_execution.[ch]`。原 `mcu_control_endpoint.c` 仅在显式
应用已挂载时转交合法 SAFE_CLOSE；`mcu_work_preparation.[ch]` 增加一次性的恢复执行器挂载
和恢复保管互斥。`actuator_runtime.[ch]` 沿用原定时器、临界区、门方向及防夹刷新逻辑，
新增独立的有界恢复关门记录和精确释放，不另建一套 GPIO 驱动。

使用 TDD 技能，从实际 C 入口缺失的失败开始，接通首条字节流→控制时序→原始反馈→Pi保存。
首条测试中误用了不存在的快照字段，按现有契约改为 `lastDeliveryDoorCommand`；未改协议字段。
后续行为回归覆盖身份/摘要、防夹、期限、停机、记录容量、配置互斥、Pi重启和确认丢失。

- 新 Python 专项 **24项**，另增加1个严格 C90 主机执行场景，直接核对写给硬件边界的输出位。
  与原底层控制组合 **34通过，6.63秒**。
- 与投递/清运执行、中断、结果交接、准备协调和P1AO恢复记录的最终相关组合
  **276通过，68.75秒**。
- 受理后输出前的两个硬件临界区抢占测试，分别注入时间到期和更新停机；实际 C 仍保留
  ACCEPTED，但反馈 OUTPUT_REJECTED，没有伪造 CLOSE 成功。
- 直接输出测试覆盖100毫秒死区、PB5暂停/继续、禁止旧入口偷跑、精确令牌释放、同向合并、
  清运锁不受影响、旧动作占用和32位时钟回绕后的64位控制时刻。
- 主机实际 C 动态库 + Pi 原交接类 + 真实 SQLite 验证三种故障：保存拒写、保存请求丢失、
  保存回复丢失。Pi重新打开数据库后，只查询/确认；原反馈字节一致，没有新的动作发送。
  这不等于已经对接永久恢复许可或原业务的自动分类。
- 完整契约校验 **25通过、0 notes**：104生成物、67消息、50黄金帧、1707流轨迹、16摘要，
  Python、Java21、C头文件及共享实现一致。
- ARMCC5/Cortex-M3 核心链接探针纳入新模块、实际公开入口和一个恢复执行器实例：
  **ROM53540字节、静态RAM6200字节**，含探针的组合32项通过、10.71秒。
  这是仅链接探针，不是可烧录固件、完整中断栈、全部驱动或机械时序合格证明。
- 四个本批 Python 文件通过3.11语法检查；相关差异空白检查通过。

所有动作发生在测试注入的硬件边界；没有打开真实串口、GPIO或摄像头，没有操作现场机构。
复用原 Python3.11、Clang、Java21、ARMCC5 环境，没有下载依赖或制作镜像。

### 最终扩大回归及保留问题

冻结代码后完整组合为 **2121通过、2失败、40跳过、1660子测试通过，373.97秒**，退出码1。
其中包含全部24项新Python专项及新增C90执行场景；未在执行期间继续修改生产或测试代码。
两项失败均保留原断言，未跳过或重试覆盖：

- `test_release_manifest_schema_matches_edge_store_current_schema`：发布清单25与候选业务库30
  未成对对齐，仍须由P7处理。本批没有调整发布声明。
- `test_committed_edge_state_survives_forced_process_termination`：Windows/Python3.11.15/
  SQLite3.50.4再次出现`SQLITE_IOERR_TRUNCATE`（1546），仍在打开连接的
  `PRAGMA journal_mode=WAL`处失败、尚未开始迁移。本批未改业务库恢复实现，根因未解决。

按diagnose技能沿用已有复现及留证流程，只读核验本次错误快照，不将重复成功当作修复。
快照为报错后依次复制，不是原子报错前镜像；未用SQLite打开或恢复原快照。目录：
`C:\Users\24217\AppData\Local\Temp\ecobin-sqlite-failure-evidence\b68a2af68e70455ba77a57bf09da6143`。

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| edge.db | 4096 | `5c9dec1886cc01f5f2307ee1ea87f9b32a4cef0f8f9a2c68beebc558013bedab` |
| edge.db-wal | 646872 | `799bfca30e0da165e9d86cecd2993970c785804eea0ba990a7ceeae9ab5c01db` |
| edge.db-shm | 32768 | `de5bdf20aea828603d6b0a1853096e1b5d309fa5592e50506536091754248aca` |

40项跳过涉及Windows无法验证的Unix接口身份、Linux权限/属主/链接、root、flock、Bash、ext4
和安装验证，不能写成已通过。最终报告位于系统临时目录，不是永久审计存储：
`C:\Users\24217\AppData\Local\Temp\ecobin-p1ap-final-regression.xml`，SHA-256为
`d6cfaa7ac6dbc8496681a896087d7173fe2370cb79c0ce6bb7cc4be8fe357ec5`。
XML已核对2163个主用例、2个失败、0个错误和24个新专项；其3823总数包含展开的1660子测试。

复跑入口：

```powershell
$py = 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe'
& $py -m pytest hardware/tests/test_mcu_safe_close_execution.py hardware/tests/test_mcu_control_modules_c.py hardware/tests/test_mcu_native_core_budget.py -q -s --tb=short
$env:JAVA_HOME = 'C:/D/002-Tools/004-DevTool/jdk-21.0.10'
$env:PATH = "$env:JAVA_HOME/bin;$env:PATH"
& $py contracts/tools/validate_contracts.py
$tests = @(rg --files hardware/tests | Where-Object {
    $_ -match '[\\/]test_(mcu_|native_|uart2|edge_store|job_safety|work_manager|updater_store|updater_agent|updater_native_|edge_boot|process_kill_recovery|business_runtime_cutover|stage4|runtime_release|business_release|local_control).*\.py$'
})
& $py -m pytest contracts/tests @tests tools/orangepi-image/tests/test_image_tooling.py -q -rs --tb=short --junitxml="$env:TEMP/ecobin-p1ap-final-regression.xml"
```

## 下一步与未完成事项

业务库仍为 **schema30**，永久库仍为schema3，UART仍为
**2.0.0-rc.20 / CLEAN_INTERRUPTION_CUSTODY_NOT_RUNNABLE**，没有修改协议布局或生成物。

本批没有把 P1AO 的“完整结果暂缺”转换为数据丢失，也没有自动申请 P1AM 的受限许可。
接下来须完成必要业务数据判定、问题归档/账本衔接，按稳定新动作身份持久准备 SAFE_CLOSE，
经过永久层授权、写前期限复核和单次发送，再把本次实际反馈核对到该新动作账本。
当前 Pi 通用命令准备器仍不接收这个 CONTROL 类指令，不能靠放行全部控制报文绕过许可。

正常恢复调度、已确认原动作/重复重启的许可处理、运行中优先关门中断语义、清运恢复代次、
后端接收、主程序、真实RS485、HMI、热点验收及成对发布仍未整体完成。已归档后又收到完整
结果的资金处理继续保留为用户待确认项；本批不作资金裁决。
发布清单仍为schema25，不能直接发布schema30候选；MCU Keil/main没有启用新增模块。
不要求用户现在烧录或操作屏幕，目标继续保持进行中。
