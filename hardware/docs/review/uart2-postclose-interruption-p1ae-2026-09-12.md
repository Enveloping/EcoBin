# P1AE：投递关门后中断原因与失败结果交接

日期：2026-09-12。承接 [P1AD 测量中断](uart2-interrupted-measurement-p1ad-2026-09-12.md)，
本批接入实际 C 投递执行器的自动检测，不再只从测试调用测量中断接口进入。
仍是本机候选：未连接设备、操作串口/GPIO、修改 HMI、部署或烧录。P3/P4/P5 未整体完成。

## 业务行为

MCU 已经记录本轮 CLOSE（关门控制输出）后，在等待行程、关门后称重、等待继续/结束选择，
或组装最终结果期间，如果前台观察到更新停止标记，或原关门控制上下文已经丢失，就冻结
独立中断原因。PB5 防夹暂停本身不是原因；CLOSE 仍是逻辑控制事实，不声称门物理到位。

例如本轮已经关门，称重只收到一个样本，随后更新停止了控制输出：

- 保留原 OPEN/CLOSE 两份记录，另存“关门后中断”，不拿中断替代真实关门输出。
- 原测量保留编号、已采样次数和实际耗时，标记被中断；无有效重量，不冒充测得零。
- 如果测量已经完成或 5 秒截止已到，保留既有终态，不用中断覆盖已确定的数据。
- 若称重尚未开始，末重明确为未采集，不取上一轮末重补齐。
- 已经形成的选择记录必须原样交接；已冻结的完整最终结果优先，不被后来的停止改写。
- MCU 等本轮原输出、中断原因、已有测量及选择完成精确保存后，再形成 FAILED（失败）结果。
- 香橙派把原始报文和业务关联提交到 SQLite 后才确认；重启或确认丢失只查询、补确认原文，
  不重新称重、开门或继续下一轮。最终结果产生唯一的等待分类任务，原业务占用仍保持。

本批只解决证据交接，不上报后端、不增加余额、不触发提现，也不凭数据交接恢复接单。
自动安全关门、接单恢复和三类业务结果的最终分类仍需要后续接线。

## 契约与记录空间

- Registry 为 `2.0.0-rc.17 / POSTCLOSE_INTERRUPT_NOT_RUNNABLE`，schema25 不变。
- 新消息 `DELIVERY_POSTCLOSE_INTERRUPTED` 为 100，正文 61 字节、完整帧 75 字节，
  使用动作事件的查询和精确保存机制，带 ACK_REQUIRED；不复用测量确认。
- 保留本次启动/事件号/前台实际观察时间、首次开门授权命令、原会话/投口/轮次、被打断的
  阶段、独立原因，以及本轮关门后测量事件号。连续轮次仍引用原授权，不伪造新 Pi 命令。
- 原因只含 `UPDATE_STOPPED`（更新停止）和 `CLOSE_CONTEXT_LOST`（关门控制上下文丢失）。
  阶段只接受上述四个关门后阶段；行程等待必须没有测量引用，另外三个阶段必须引用先前
  非零测量事件号。Python/C/Java 同步拒绝阶段和引用矛盾。
- 首次授权受理前除 OPEN/CLOSE 两个位置外，再独立预留一个中断记录位置及全局编号额度。
  三处任一不足都先拒绝且撤回尚未发布的预留，不执行动作；继续投递复用整次业务的中断预留。
- 正常结果冻结后取消未用中断预留；如果 CLOSE 之前已经冻结独立动作中断，立即取消这个
  不再可能使用的关门后预留，原 OPEN/动作中断记录仍保留。绝不撤销已发布或未保存的事实。
- 八条动作池、64 字节正文容量不变。没有 OneNet 模型扩展、后台版本放行或数据库迁移。
- 当前共 **66 消息 / 52 完整守卫 / 47 黄金帧 / 1421 流轨迹 / 12 摘要样例 / 104 生成物**。
  新增黄金帧和各阶段、原因、测量引用的共享语义轨迹；旧运行生成制品仍冻结。

## 实现位置

- `hardware_mcu/USER/mcu_delivery_execution.c/.h`：第三处预留、自动检测、原测量终结、
  原选择/输出精确保存检查、失败结果组装、跨轮次重量下降异常保留及预留取消。
- `mcu_control_endpoint.c`、`mcu_actuator_event_journal.c`：新消息关键事件标记、动作池支持、
  正文容量静态约束。仍通过既有原文查询、消息类型/事件号/摘要精确确认交接。
- `contracts/uart/uart-registry.yaml`、schema、生成器和动作事件参考校验：唯一机器来源与
  Python/C/Java 同步语义守卫；生成到独立候选，不覆盖现场协议。
- `hardware/tests/test_mcu_postclose_interrupt.py`：实际生产 C 编译为主机库，经真实 Python
  客户端与合成 SQLite 验证；物理采集/时间/GPIO 在测试硬件边界注入，不是目标板运行。

## 测试过程与证据

按 TDD 小步推进：先观察新消息缺失、错误阶段/引用被接受，以及实际 C 停止后没有独立原因，
再分别实现契约守卫、第一条关门等待中断和后续测量/选择/重启场景。

- 新增 24 项契约专项：参考校验、生成 Python 和实际 C 的合法/非法正文；共享轨迹再覆盖
  Java、全部阶段与两种原因，并运行 C 独立头文件和共享实现两种形式。
- 新增 21 项实际 C/Pi/SQLite 专项：关门等待；部分测量/已完成测量；已有继续/结束选择；
  已保存选择/已冻结正常结果优先；独立控制上下文丢失；连续第二轮及前轮重量下降异常。
- 其中 10 项分别丢中断保存、过程保存和最终保存的请求/回复后重启 Pi。发送确认之前使用
  独立数据库连接验证事务提交、原文和原业务占用；只发送查询与保存确认，待分类任务唯一。
- 测试曾尝试用旧 `ActuatorRuntime_SetDoorTarget` 模拟上下文丢失，但原生投递模式正确拒绝
  旧入口。没有削弱这条隔离；增加仅主机测试可用的执行器组件重新初始化入口，保留原时钟和
  MCU 会话。它证明组件上下文丢失分支，不证明实际 MCU 重启恢复。
- 三处旧测试预期同步：更新停止现在出现独立中断事件；容量测试考虑第三处业务预留；
  CLOSE 前中断后释放未用第三处预留。最后一项通过生产取消逻辑修正，没有放宽原空间断言。
- 最终相关组合 **192 项通过，24.61 秒**。新专项单独 **21 项通过，4.35 秒**。
- 完整契约验证 **25 项通过、0 notes**，包括 Java21 和两种 C 构建方式。
- ARMCC5 核心预算测试 **1 项通过，4.04 秒**：Code 42136 / RO 1880 / RW 56 / ZI 5688，
  ROM **44072** / 静态 RAM **5744** 字节；比 P1AD +1240 / +24 字节。
  仅 DO_NOT_FLASH 核心链接探针，不包含完整启动/驱动/中断栈证明，不能烧录。
- Clang 静态分析本批三个生产模块无警告。P1AD 记录的底层称重两个前置条件告警仍未宣称
  修复，也未通过抑制或猜测重量消除。
- 最终扩大回归 **1412 通过 / 5 跳过 / 1635 子测试通过 / 零失败，267.98 秒**。
  五项跳过分别为 Windows 缺少 root 独立服务身份、POSIX flock、Unix 对端身份认证及两项
  POSIX 权限位验证。只覆盖下述契约和硬件相关集合，不等于全仓库、全部硬件套件或真机验收。
  SQLite 强杀恢复本轮通过；既有偶发 1546 根因仍未确定，不能因此宣称已修复。

定向命令（复用既有 Python 3.11、Java21、LLVM 与 ARMCC5 环境）：

```powershell
& 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe' -m pytest hardware/tests/test_mcu_postclose_interrupt.py hardware/tests/test_mcu_delivery_execution.py hardware/tests/test_mcu_delivery_continue.py hardware/tests/test_mcu_delivery_abort.py hardware/tests/test_mcu_delivery_postclose.py hardware/tests/test_mcu_delivery_finalization.py hardware/tests/test_mcu_interrupted_measurement.py hardware/tests/test_mcu_actuator_event_journal.py contracts/tests/test_uart_v2_postclose_interrupt.py -q --tb=short
```

扩大回归命令（JAVA_HOME/PATH 预先指向本机 Java21）：

```powershell
$tests = @(rg --files hardware/tests | Where-Object {
    $_ -match '[\\/]test_(mcu_|native_|uart2|edge_store|job_safety|work_manager|updater_store|edge_boot|process_kill_recovery|business_runtime_cutover|stage4).*\.py$'
})
& 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe' -m pytest contracts/tests @tests -q -rs --tb=short
```

## 仍未完成的边界

本批不是“任何异常都能自动收口”：只覆盖已有 CLOSE 后四个阶段的更新停止或控制上下文丢失。
继续轮次因配置/外部准入等被拒绝而进入安全锁定的完整失败交接，尚未全部实现。共享 32 位
事件编号耗尽时，如果仍需发布测量过程记录，不能挪用动作预留或谎称未采集；可能保持原数据
和占用等待后续处理。不能宣称所有编号耗尽路径都已终结。

实际 RS485 迟到回复归属、完整配置消费者、清运执行/再次开锁、HMI、香橙派永久动作账本效果
核对、重启恢复/分类、正常 main、后台三类业务数据效果与成对发布仍待完成。
Windows SQLite 强杀恢复偶发 1546 问题未宣布修复，P1AB 测试内取证继续保留。
当前无需用户提供 SSH、修改屏幕或烧录。
