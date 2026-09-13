# 原生 UART 2 完整结果可靠交接 P1C

日期：2026-09-12。范围：本地契约、MCU 独立结果保留模块、真实 EdgeStore 事务。
承接 [P1B](uart2-session-foundation-p1b-2026-09-12.md)，不代表整套协议或恢复流程完成。
本批未 SSH、部署、烧录、刷卡、操作 GPIO、变更云端物模型或发布固件。

## 已实现的行为

1. MCU 完成投递/清运后，可以冻结一份自包含结果：业务/原命令身份、配置版本、结束原因、
   次数/清运动作序号、人工关门确认及前后两份测量事实。照片、袋子代次、价格和用户仍由 Pi 保存。
2. 实际正文 199 字节、完整帧 213 字节，低于 256 字节上限，因此不引入结果分片。
   测量缺失明确使用 NOT_TAKEN / MCU_RESET_LOST，槽的零编码不等于测得 0 克。
   真实稳定 0 克/负值可以表示；超时中位数单独标识，不冒充稳定或旧四点均值。
3. SHA-256 总摘要覆盖结果身份和全部正文；CRC 正确但摘要或字段关系错误，也不能进入交接。
   MCU 冻结后不重算结果；重复查询读回原字节，不执行动作。
4. Pi 的 `EdgeStore.save_native_mcu_result(bytes)` 完整解码校验后，使用真实数据库的
   WAL + synchronous=FULL + BEGIN IMMEDIATE，将完整原字节和待上报任务同事务提交。
   只有 COMMIT 成功后才返回 `savedPayload`，它正好是 RESULT_SAVED 的 60 字节身份正文。
5. 相同启动/结果序号的完全重复正文只返回原任务身份；不同正文保留冲突证据、拒绝确认，
   不覆盖原结果。两个数据库连接同时交接同一结果，也只有一个任务。
6. MCU 只接受匹配启动/结果号/业务 UUID/总摘要的保存确认。旧确认不释放下一份结果；
   最近已释放结果可以重复确认。更早的细节可返回 NOT_FOUND，但不能解释为从未执行。

## 业务边界：待上报不等于已上传

`native_result_report_outbox` 是新协议专用的持久待处理队列，目前状态只有
`PENDING_CLASSIFICATION`，意为“原始结果已保管，等待香橙派结合业务上下文决定如何上报”。
它不是现有 `event_outbox`，不会被当前云端发送器自动消费。

因此本批交接不释放业务占用、不变更袋子、不建订单、不增加余额、不触发提现。
后续分类器需要关联照片、会话、原袋、新袋及异常归档，再原子生成合适的 OneNet 上报事件。
正常完成、真正称重失败、MCU 重启丢数分别沿已确认规则处理；已归档后迟到完整结果的
资金处置仍未确认，本批只保存证据，不擅自选择重新结算。

当前未接真实串口循环，所以并没有在设备上发送新的保存确认。

## 契约与文件

- 唯一可变 Registry：`contracts/uart/uart-registry.yaml`，版本 `2.0.0-rc.3`，
  `RESULT_HANDOFF_NOT_RUNNABLE`。51 条消息；本批增加 WORK_RESULT / QUERY_RESULT /
  RESULT_QUERY_REPLY，三语言内容校验覆盖累计 12 条新引导/会话消息。
- 新 `ResultMeasurementKind` 是完成结果的组合状态，不修改旧 WeightValueKind / OneNet。
  字段/枚举/摘要和语义以 Registry 为准；Python 公共规则由生成器嵌入，C/Java 等价规则由生成器输出。
- 新生成 `hardware/uart2_protocol.py` 是明确命名的候选解码器；仅新交接 API 延迟导入。
  正常入口仍只启现有模式，没有自动识别、双解析或失败回退。
- `hardware_mcu/USER/mcu_result_slot.c/.h` 为前台单所有者 RAM 模块，不在 Keil 工程/main 中。
  只在 MCU 启动/绑定时初始化，不能因 Pi 断连而重置。调用方负责原业务匹配及动作占用。
- 冻结的 `hardware/uart_protocol.py`、`USER/uar/ecobin_uart_protocol.h` 和旧黄金程序
  三个文件未变；`--include-hardware-mcu` 仍拒绝覆盖导出。

## 数据库与发布边界

EdgeStore 与安装清单同步为 schema 19，增加：

- `native_mcu_result`：不可变完整结果；
- `native_result_report_outbox`：与结果一起提交的待上报任务；
- `native_mcu_result_conflict`：同身份冲突正文。

18→19 使用既有迁移事务，并保留旧 v17/v18 完整性复查。中途退出时，DDL 与版本号一起回滚；
升级后原业务占用/上下文保持。禁止嵌套交接事务，且拒绝嵌套不会回滚调用者原事务。

安装包清单包含独立候选解码器，防止未来出现代码在开发目录能导入而包中缺文件。
这不放开跨 schema 自动激活：旧卡 schema 18 不能直接当成与此候选兼容，发布仍须另做保留数据
的迁移/回退及成对切换审查。没有制作发布包或镜像。

## 测试与资源证据

按 TDD 先复现缺少接口/缺少结果保留模块，再实现；另先复现事务嵌套会伤及外层事务、
UUID 不同文本表示绕过前后测量去重，随后修复并保留回归测试。

- 真实 SQLite：任务写入前退出、写入后但提交前退出、提交后但确认前退出；使用子进程
  `os._exit`，不依赖正常关闭。另有 COMMIT 被 SQLite 拒绝、迁移版本写入时退出、并发重复测试。
- 所有 212 个可能的单帧拆字节位置、半帧超时、重复完整帧；缺值/实测零、中位数、错误身份、
  同编号异文、重算摘要后仍不合法的业务/测量字段组合。
- 契约验证器：24 项通过、0 notes；103 个生成文件一致；27 个帧向量、229 条流轨迹、
  4 个摘要向量在 Python 3.11 / Java 21 / C11 通过。
- MCU 结果保留模块：Clang C99 警告视为错误的行为测试通过；ARMCC 5.06 update 5 build 528，
  Cortex-M3 / C99 / O2 编译无错误和警告。保留状态静态预算 ≤224 字节（当前布局 216 字节）。
- 独立未链接对象 `.text=3972`、`.constdata=286` 字节，包括结果校验与 SHA 实现；不是整机 ROM。
  反汇编所见 Freeze→校验→摘要→update→transform 栈帧合计 408 字节，不含调用者、库和中断；
  不能据此宣称原 512 字节栈足够。接入前仍需完整链接调用图及中断预算。
- 最终定向 pytest：301 passed、31 skipped、1282 subtests passed（44.02秒）；复现命令见下方。
  Windows 无 POSIX权限/文件锁/链接及相关工具的安装用例跳过，不能算作 Linux 真机或更新验收。
  `git diff --check`通过，仅既有换行风格转换提示。

```powershell
& 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe' contracts/tools/generate_contracts.py --check
& 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe' contracts/tools/validate_contracts.py
& 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe' -m pytest contracts/tests hardware/tests/test_native_result_handoff.py hardware/tests/test_mcu_result_slot_c.py hardware/tests/test_edge_store.py hardware/tests/test_mcu_session_c.py hardware/tests/test_mcu_control_modules_c.py hardware/tests/test_process_kill_recovery.py hardware/tests/test_runtime_release_install.py hardware/tests/test_business_runtime_cutover.py hardware/tests/test_image_software_installer.py -q --tb=short
```

## 下一批

1. QUERY_WORK 与一致状态快照：按原业务查询运行中/结果可取/明确不存在，补门目标/实际输出/
   PB5、测量与配置事实；不能以单个回复证明当前可接单。
2. 实际 Pi 启动/命令编号持久分配、单次写入发送及超时只读查询，接入正常单一串口所有者。
3. MCU 业务状态机、结果组装/摘要生成与测量核心接线；本批模块只验证并保存已经组装好的正文。
4. 结果分类器及 OneNet/后端中位数、重启问题记录、缺旧重清运合同同步；保持手动隔离账本边界。
5. 整机链接/栈/时序预算及本地故障注入通过后，再安排用户手动烧录和安全真机验收。

不把 P1C 局部完成写成 P0/P1/P3 整体关闭。HMI 和迟到结果资金取舍仍保留未决状态。
