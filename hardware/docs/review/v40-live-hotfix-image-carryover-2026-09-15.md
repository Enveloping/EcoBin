# v40 实机热修复与新镜像继承清单

日期：2026-09-15。对象：当前已经完成热点厂家硬件验收（P7 HIL）和设备注册，随后进入生产
运行切换的 v40 HIL TF 卡。这里的“通过”只指热点内 MCU、传感器、称重、投递机构和清运机构
验收，不包含生产 OneNet、订单、COS、余额或提现闭环。

本文的用途不是宣称 v40 已经可用，而是防止现场对已安装程序做过热修复后，新镜像只包含
部分修改，导致同一故障再次出现。后续修改、构建和验收必须先读本文，再读
[`v40 热点验收 UART 与称重零点修复`](v40-hotspot-acceptance-repair-2026-09-14.md)。

## 1. 状态词

- **仓库已提交**：修改已经进入 Git 提交，新镜像从包含该提交的源码构建即可继承。
- **仓库未提交**：当前工作区已有修改和测试，但尚不能作为可复现镜像输入。
- **当前卡已热修**：文件已经直接替换到当前 v40 卡；重新烧卡后会丢失，必须由仓库源码和
  镜像构建重新带入。
- **未解决**：已经确认问题及触发条件，但解决方案尚未完成；在解决前不得构建并称为下一版
  可验收镜像。

## 2. 当前实机事实

截至本文创建时，实机事实如下：

- MCU 固件与 v40 热点厂家硬件验收已经通过；工厂报告为 `PASSED` 且报告校验通过。这不是
  生产业务全链路通过。
- SIM 重新安装后，Air780E 返回 `+CPIN: READY`，网络注册为 1、分组附着为 1；RNDIS 网卡
  获得 `192.168.10.2/24` 和默认路由 `192.168.10.1`。
- 设备凭证 `/etc/ecobin/device-credentials.json` 已存在，首次启动阶段为
  `ENROLLMENT_COMPLETE`；这证明设备注册已经完成，但不等于生产业务服务已经正常运行。
- 工厂封存文件 `/var/lib/ecobin/first-boot/sealed.json` 尚不存在；永久业务运行时切换的
  `/var/lib/ecobin/privileged/business-runtime-cutover/pending.json` 和
  `/var/lib/ecobin/privileged/business-runtime-cutover/active.json` 都不存在。
- `ecobin-remote-support.service`、`ecobin-updater.service` 和旧直连
  `ecobin-hardware.service` 已运行；因永久运行时切换标记尚不存在，OneNet 和 UART 仍由唯一的
  旧直连业务进程持有，永久通信代理不接管 OneNet。
- `ecobin-hardware.service` 的首次启动门禁和永久作业授权构造均已通过；OneNet 已连接、订阅并
  收到平台回复。永久作业闸门仍为 `LOCKED / STAGE4_ACTIVATION_REQUIRED`，因此没有放行投递或
  清运。
- MCU 已绑定启动编号 `8000000000000002`。重启验证暴露出的后台状态读取与前台 UART 轮询
  时钟竞争已经精确修复并热部署；受控清除该次泛化锁存后，当前状态为
  `RUNNING / READY / CONNECTED`。

因此当前卡的准确结论是：**蜂窝、注册、生产进程、UART v2 和 OneNet 已恢复；热点验收报告
有效；永久作业仍安全锁定。反向 SSH 授权和完整生产投递/清运尚未完成实机证明。**

## 3. 已确定并已提交的 v40 后续修复

### 3.1 热点动作请求在线程切换后无法使用 UART

- 现场现象：点击“检查控制板和传感器”返回 502，称重变送器没有收包指示。
- 根因：验收主线程打开 `/dev/ttyS5`，HTTP 请求却由另一个线程执行；原生 UART 要求打开、
  发送和读取由同一个前台线程持有。
- 解决方案：厂家 Unix socket 服务改为单线程串行处理，保持有界客户端超时。
- 仓库状态：提交 `780d2ecb`。
- 主要文件：
  - `hardware/factory/acceptance_service.py`
  - `hardware/factory/native_acceptance_mcu.py`
- 当前卡：已热修并用真实 MCU/RS485 请求验证。

### 3.2 350 kg 秤的稳定负零点被错误拒绝

- 现场事实：连续读取约 `-25623 g`，通信、地址、波特率、数据格式和 CRC 均正常。
- 根因：工厂程序把绝对重量限制为 `-5000..350000 g`，但验收本来依据加载前后差值和卸载
  回零差值，不应把绝对零点当成准确度结论。
- 解决方案：350 kg 量程内接受 `-350000..350000 g` 的有符号原始重量；参考物仍为正数且
  不超过量程，误差和稳定性规则不变。
- 仓库状态：提交 `780d2ecb`。
- 主要文件：
  - `hardware/factory/acceptance_hardware.py`
  - `hardware/factory_seal/validation.py`
  - `hardware/factory_seal/weight_validation.py`
- 当前卡：已热修并完成真实称重验收。

### 3.3 辅助传感器不可用错误阻断验收或最终结果

- 已确认边界：烟感、超声波/满溢和摄像头是辅助事实；不可用时展示和留证，但不单独阻断
  投递或清运。称重仍是业务必要数据。
- 第一处修复：验收允许辅助传感器明确上报 `UNAVAILABLE/NOT_OBSERVED`，不伪装为正常；提交
  `780d2ecb`。
- 第二处修复：MCU 最终结果中的辅助事实不可用时，不再对启动默认采集时间 0 执行“数据过期”
  校验；提交 `5bf89e67`。
- 主要文件：`hardware/factory/native_acceptance_mcu.py`。
- 当前卡：两次修改均已热修；投递结果从 `MCU_FULLNESS_FACT_STALE` 恢复，未重发 START，
  2026-09-15 后续现场完成投递区域安全确认和完整清运，厂家验收最终为
  `PASSED/COMPLETE`、revision 41。前一日修复记录停留在投递安全确认前，本条后续事实取代其
  “尚未完成”状态；这仍只是厂家 HIL，不是生产订单或资金闭环。

### 3.4 MCU 启动编号宽度

- 现场编号 `8000000000000001` 是 UART rc.26 允许的安全整数，不是 32 位数。
- 解决方案：验收清洗和报告链不再把启动编号误限为 32 位。
- 仓库状态：提交 `780d2ecb`。
- 当前卡：已热修并通过真实身份检查。

上述提交同时修改了热点页面事实展示和相应测试；新镜像必须从包含
`780d2ecb`、`5bf89e67` 的提交构建，不能再从原 v40 构建提交直接重制。

## 4. 本轮已确定、仓库尚未提交的修复

### 4.1 Air780E 数字 CME 错误未识别为 SIM 缺失

- 现场原始回复：`AT+CPIN?` 返回 `+CME ERROR: 10`。
- 根因：代码只识别英文 `NOT INSERTED` 或 `SIM ABSENT`，把数值 10 错误显示为泛化的
  `CELLULAR_MODEM_STATUS_UNAVAILABLE`。
- 已实现方案：精确把 `+CME ERROR: 10` 映射为 `CELLULAR_SIM_ABSENT`；其他 CME 数值仍保持
  泛化失败，不能误报。
- 仓库文件：`hardware/first_boot/cellular_modem.py` 及其测试。
- 当前卡：Python 文件已热修到厂家运行目录；SIM 已恢复，错误分支尚需在下一镜像用拔卡负例
  再验一次。

### 4.2 完成注册后，短时断网不应阻止本地运行时启动

- 现场现象：蜂窝已注册并完成设备注册，但 HTTPS 探测偶发失败时，首次启动状态退回
  `UPLINK_REQUIRED`，`ecobin-hardware.service` 的运行门禁被跳过。
- 根因：首次启动状态机和 `runtime` 门禁仍把当前 `uplink_ready/time_trusted` 当作每次生产
  启动的必要条件。这与既定规则冲突：注册前需要联网和可信时间；注册完成后，断网只应停止
  新业务并保留本地结果待补报，不能阻止本地业务程序启动。
- 已实现方案：
  - 已有可靠注册凭证且流程已经到达注册阶段后，不因当前断网退回厂家联网阶段；
  - 生产运行门禁继续要求本次启动 GPIO 安全、有效工厂报告、注册凭证和安全交接；封存文件
    不存在时允许进入待封存生产通信，存在时则必须校验有效；门禁不再要求当下 HTTPS 成功；
  - 已封存设备冷启动时也直接启动运行目标；蜂窝服务由运行目标继续管理和重试。
- 仓库文件：
  - `hardware/first_boot/state_machine.py`
  - `hardware/first_boot/orchestrator.py`
  - `hardware/tests/test_first_boot_state_machine.py`
  - `hardware/tests/test_first_boot_orchestration_actions.py`
- 当前卡：两份 Python 文件已热修，逐项事实显示 `runtime-gate=True`。

这里没有设计为放宽新业务准入。等第 5 节生产启动阻断解决并完成实机验证后，原生业务仍应在
`_check_start()` 中要求实时云连接：断网期间已开始的业务可保存结果并等待补报，新业务仍被
拒绝。当前卡的主业务仍在构造阶段退出，尚不能用它证明这条运行行为。

### 4.3 systemd 门禁被业务工作目录中的同名 `factory` 包遮蔽

- 现场对比：
  - 从厂家运行目录执行门禁：工厂报告 `PASSED/valid`，运行门禁为真；
  - 从硬件运行目录执行同一命令：导入硬件包内同名 `factory` 模块，报告被判为
    `FAILED/FACTORY_REPORT_INVALID`，运行门禁为假。
- 根因：`python -m first_boot.gate` 默认把当前工作目录放在模块搜索路径最前面。虽然 unit 设置了
  `PYTHONPATH=/opt/ecobin/factory-test/current/app`，但硬件服务的
  `WorkingDirectory=/opt/ecobin/hardware/current/app` 仍能优先遮蔽厂家验收依赖。
- 已实现方案：首次启动 systemd 源 unit 中的 gate 命令使用 Python 3.11 的 `-P` 安全路径
  选项，不把当前工作目录自动放到模块搜索路径前面；仍从明确的 `PYTHONPATH` 加载厂家门禁。
- 仓库文件：
  - `hardware/first_boot/systemd/ecobin-runtime-gate.service`
  - `hardware/first_boot/systemd/ecobin-cellular-uplink.service`
  - `hardware/first_boot/systemd/ecobin-factory-test.service`
  - `hardware/first_boot/systemd/ecobin-factory-handoff.service`
  - 三个 enrollment/hardware/remote-support 的 `20-first-boot-gate.conf`
  - `hardware/tests/test_first_boot_systemd_units.py`
- 当前卡：目前只热修了
  `/etc/systemd/system/ecobin-hardware.service.d/20-first-boot-gate.conf`；实机已证明该服务的
  ExecCondition 从状态 1 变为状态 0。其余六个 unit 修改只在工作区，下一镜像必须全部带入。

### 4.4 设备管理维护安装器内嵌 gate 已同步

- 新发现：`hardware/system/device_management_maintenance_installer.py` 内嵌了
  `ecobin-hardware.service` 的旧 `20-first-boot-gate.conf` 正文及固定 SHA-256，正文仍没有
  `-P`。
- 影响：即使新镜像初始 unit 正确，后续执行设备管理层安装、审计或回退时，也可能因为内嵌
  基线不一致而失败或把旧 gate 恢复回来。
- 当前证据：
  `hardware/tests/test_device_management_maintenance_installer.py::test_embedded_v13_gate_baseline_matches_the_image_source_file`
  已失败，证明源 unit 和内嵌副本已经分叉。
- 已实施方案：同步更新 `LEGACY_GATE_DROP_IN_CONTENT` 和
  `LEGACY_GATE_DROP_IN_SHA256`，继续用测试要求内嵌正文逐字节等于镜像源 unit。
- 验证：原先精确失败的一致性测试与首次启动 systemd unit 专项合计 `13 passed`。
- 当前卡：没有运行维护安装器，也没有热修其 Python 文件；仓库缺口已经修复，但只有重新构建
  镜像或执行正式维护发布后才会进入设备。

### 4.5 当前专项测试

修改前新增用例得到 4 个预期失败：注册后断网退回、离线运行时不启动、封存后离线只启动蜂窝、
CME 10 未识别。修复后，加上 systemd 安全导入路径用例，当前专项结果为：

```text
62 passed, 1 skipped
```

这只是首次启动、编排、蜂窝 AT 和 systemd unit 专项，不代表完整 `hardware/` 回归或真实冷启动
验收已经完成。第 4.4 节原先失败的一致性测试已另行与 systemd unit 专项合计得到
`13 passed`；维护安装器完整测试仍须在最终扩大回归中执行。

## 5. 生产启动阻断的修复过程

### 5.1 UART v2 与永久作业授权模式不一致

门禁修复后，`ecobin-hardware.service` 已经真正进入 `main.py`，随后稳定失败：

```text
ValueError: native business requires the permanent job authority
```

已确认的配置组合是：

- `ECOBIN_MCU_PROTOCOL=uart-v2`，因此 `main.py` 创建 `NativeBusinessRuntime`；
- 旧 root 服务 `ecobin-hardware.service` 固定使用
  `ECOBIN_BUSINESS_CONTROL_MODE=status`；
- 永久业务运行时切换标记不存在，因此系统仍选择旧 root 服务，而不是
  `ecobin-business-runtime.target`；
- `NativeBusinessRuntime` 明确要求启用的 `PermanentJobSafety`，而旧服务没有启用 stage 4
  永久作业门。

这不是 SIM、OneNet 注册、工厂报告或 MCU 通信错误，而是 **v40 把正常协议切成 UART v2 后，
生产服务选择和作业授权模式没有一起收口**。

### 5.2 架构核对结论

核对既有一次性运行时切换设计、首次启动协调器、旧/新 systemd 单元和 v32 真机记录后，排除
了“伪造切换标记”或“同时启动两套业务链”：没有切换标记时仍必须由旧 root 业务进程直接
连接 OneNet；只有受控切换完成后，才由永久通信代理和低权限业务进程接管。

本次缺口来自两个先后完成的改动没有在生产启动入口相遇：第四阶段的永久作业许可原来是
默认关闭候选，而后来的 UART v2 已把该许可作为正常业务的硬依赖。正确的最小收口是把
**永久作业闸门和动作账本**提升为旧直连过渡链的正式依赖，同时保持软件更新能力关闭：

- `ecobin-updater.service` 增加
  `--enable-stage4-candidate --allow-root-business --business-uid 0`，允许旧 root 业务进程申请
  作业许可；其中 `--allow-root-business` 是新增的显式迁移姿态，没有该参数时原来的非 root
  限制不变；不增加 MCU 更新、业务程序更新或远程更新开关；
- `ecobin-hardware.service` 增加 `ECOBIN_STAGE4_JOB_GATE_MODE=candidate`，但继续使用
  `ECOBIN_BUSINESS_CONTROL_MODE=status`，因此它仍只暴露只读本机状态，不取得版本切换能力；
- 新数据库或新候选周期仍以“需要激活、作业门锁定”启动。进程可以上线 OneNet、接受厂家
  封存授权和上报状态，但在带证据激活前不能开始投递或清运；
- 一次性切换标记的语义不变。切换后仍由 `ecobin-updater-candidate.service` 和
  `ecobin-business.service` 组成低权限新链，旧服务按原条件停止。

这同时满足以下原约束：

1. 未封存但已注册的设备必须有一个真实 OneNet 所有者，能够接收后台封存授权；不能形成
   “先封存才启动 OneNet、先启动 OneNet 才能封存”的循环依赖。
2. UART v2 的正常 START、结果保存、结果确认和恢复路径使用同一种作业授权模型；不能只删除
   构造器检查，让后续 `get_job_permit()`、`complete_job()` 在业务进行中再崩溃。
3. 旧 root 链与低权限新业务链只能有一个拥有 UART、OneNet 和业务槽；不能为了启动成功让
   两套服务并行。
4. 新业务是否允许开始仍由实时云连接、有效配置、称重、MCU 通信和本地故障锁判断；修复启动
   入口不能绕过这些准入。
5. 工厂验收数据不得迁移成用户订单或资金价值；首次独立业务库的基线、当前袋和皮重必须按
   既有切换设计迁移或重建。
6. 方案必须有冷启动、服务重启、OneNet 封存、投递、清运和断网恢复测试，不能只以进程
   `active` 为完成。

### 5.3 同一路径发现的第二个退出点

`_run_native()` 在已经打开串口、启动本机控制服务后调用
`notify_systemd_ready("NATIVE_STARTING")`，但允许列表遗漏了 `NATIVE_STARTING`。因此即使修好
永久许可，进程仍会在向 systemd 报告就绪时抛出 `RuntimeError` 并退出。它不是机械业务已经
就绪的声明：此时只是进程能够处理管理、上报和继续完成 MCU 身份/配置收敛；实际 START 仍受
永久作业门、云连接、配置、称重、通信和故障锁共同约束。

已把 `NATIVE_STARTING` 加入允许的进程就绪状态，并新增直接调用真实
`notify_systemd_ready()` 的回归用例，避免原生入口测试继续用 monkeypatch 掩盖该错误。

### 5.4 当前仓库验证与剩余现场门禁

先新增上述两个真实组合用例，修改前稳定得到 3 个预期失败：原生就绪状态被拒绝、旧更新器
未启用永久闸门、旧业务服务未选择永久许可。实施后 systemd/就绪专项为：

```text
18 passed
```

扩大回归随后得到 `296 passed, 6 skipped`；镜像安装器、维护安装器、封存和发布保管组合得到
`459 passed, 39 skipped`。跳过项均为当前 Windows 不能证明的 POSIX/Linux 环境条件。

第一次实机热修准确暴露出自动化遗漏：更新器拒绝 `--business-uid 0`，错误为
`--business-uid must be positive and non-root`。现场立即停止失败循环，没有伪造更新器状态。
根因是旧直连程序的 root 身份与原候选只允许低权限业务 UID 的边界尚未显式表达。仓库随后
增加上述 `--allow-root-business`，并硬性限制它只能与 stage 4 作业闸门一起使用、业务 UID
只能为 0，而且 MCU 更新、业务程序更新、软件状态发布和远程更新任一开关存在时都拒绝启动；
默认和切换后低权限模式仍拒绝 root。对应更新器与 unit 专项为 `38 passed, 3 skipped`。

第二次热修后，实机 `ecobin-updater.service` 和 `ecobin-hardware.service` 都进入
`active/running`，硬件服务重启计数停在 18，OneNet MQTT 已连接、订阅成功并取得平台回复；
更新器状态为 `ENFORCED / LOCKED / STAGE4_ACTIVATION_REQUIRED`、0 个活动许可、无维护锁，证明
它没有因修启动入口而放行投递或清运。

本次启动又留下一个新的阻断事实：MCU 已重新取得启动编号 `8000000000000002` 且本机控制接口
显示当前通信新鲜，但首次运行在 2026-09-14T18:00:04Z 捕获一个没有稳定错误码的异常，只保存
为 `NATIVE_RUNTIME_FAILED`，没有对应可恢复故障编号，导致热点进度仍显示 UART 失败。仓库先把
捕获点改为记录完整 Python 栈（不记录业务载荷），服务重启后在 18:08:11 精确得到：

```text
NativeBusinessRuntime.poll
  -> _device_entry_url_poll
  -> McuBootSession.current_boot
ValueError: session clock must be non-negative monotonic milliseconds
```

这不是香橙派重启后读取了上次启动的单调时钟：该值没有写入 SQLite，新进程的 MCU 会话从空
内存状态创建。真实原因是同一进程里的线程竞争：

1. 前台 UART 轮询先读取时间 `T1`；
2. 厂家进度、本机状态或维护接口的后台只读线程经 `uart_state`、
   `communication_fault_status()` 等属性再次进入实时 MCU 会话，并用稍后的 `T2` 推进其内部
   时钟；
3. 前台恢复执行后仍把先前的 `T1` 传给该会话，因 `T1 < T2` 被误判为时钟倒退。

代码原有注释已经规定后台只能读取前台发布的不可变运行快照，但
`current_mcu_boot_id`、`mcu_session_ready`、`current_device_facts()`、`uart_state` 和
`communication_fault_status()` 的实现没有完全遵守。当前仓库已统一改为读取前台每轮完成后
原子替换的快照；后台不再调用 UART 会话时钟或读取可变查询对象。前台仍保留严格单调检查，
不能用“允许倒退”掩盖真实并发错误。先写的失败用例能够稳定证明旧实现会触碰实时会话，修复后
该用例通过；随后增加 `threading.Event` 强制重现“前台持有 T1、后台在 T2 查询、前台继续使用
T1”的确定性交错，并断言不新增原生命令、不改变业务槽。

只把实时状态换成快照仍不完整：如果前台轮询被阻塞，后台可能长期误用最后一份
`READY / fresh=true`；如果主程序先取一次运行快照、再单独读取 `uart_state`，还可能把两个
不同轮次的状态拼在一起。当前仓库已继续收口：

1. `McuBootSession` 在一次受控前台调用中同时返回启动编号和该启动证明的排他截止时间；
2. 前台发布的不可变快照同时保存启动、设备事实、通信新鲜度各自的有效截止时间；
3. 后台只用独立的本机单调时钟判断快照是否过期，不推进 UART 会话内部时钟；过期后统一显示
   `STARTING`、启动编号 `0`、无新鲜设备事实且通信未恢复；
4. `uartState` 与启动编号、身份和设备事实放在同一份快照中，主程序一次读取，不再混用两个
   轮次；
5. 过期快照不能作为清除人工通信故障或通过厂家检查的证据。

对应单元测试覆盖浅层只读入口、确定性交错、快照过期、过期快照不能清故障、同代
`uartState` 以及启动窗口截止时间；当前所选设备二维码、原生业务、控制故障和运行快照组合为
`58 passed`。全量 `hardware/tests` 仍需在本节最终实现上重新执行，不能沿用收口前曾运行到
40% 的中止结果。

随后从头执行一次全量 `hardware/tests`，得到 `4279 passed, 126 skipped, 5 subtests passed`。
该轮虽无失败，但独立源码与镜像路径审查又发现四个自动化测试尚未覆盖的边界，因此不能把这次
结果当作最终构建结论：

1. 七个首次启动 gate 都已使用 Python 3.11 的 `-P`，但蜂窝上联、厂家测试、厂家交接三个 unit
   没有设置 `/opt/ecobin/factory-test/current/app` 为 `PYTHONPATH`。镜像使用
   `uv sync --no-install-project`，源码只复制进 `app`，所以这三个服务在新卡会无法导入
   `first_boot`；
2. 快照过期后虽已隐藏身份和设备事实，公开运行快照仍带着上一轮 `mcuBootId` 和能力位，与
   `STARTING` 同包上报；
3. root UART-v2 业务入口已启用永久作业闸门，但 `ecobin-hardware.service` 没有声明更新器启动
   顺序。首次启动编排显式先启动 hardware，hardware 又在占用 UART 前访问 updater socket，
   冷启动会形成 `JOB_GATE_UNAVAILABLE` 竞态；
4. 持久性原生轮询异常会按 50 ms 周期反复打印完整 Python 栈；厂家进度映射还会在一次调用中
   读取两次 `uart_state`，存在混合快照轮次的窄窗口。

当前仓库已分别修复：三个 unit 显式设置受控 `PYTHONPATH`，并新增按镜像“源码复制但项目不
安装”布局实际执行 `python -P -m first_boot.gate --help` 的子进程测试；公开快照过期后将当前
启动号和能力位都置零；hardware 以 `Wants + After` 等待 `Type=notify` 的 updater 已绑定本机
socket 后再启动，但不使用 `Requires`、`BindsTo` 或 `PartOf`，以免更新器维护重启时杀掉正在
收取 MCU 最终结果的硬件进程；持久错误只为每种有限的“错误码 + 异常类型”记录首份完整栈，
重复项每分钟汇总一次；厂家进度只读取一份 UART 状态。更新器不可达时仍在打开串口前失败，
不产生 UART 写入，新业务也不会绕过永久闸门。该阶段新增定向回归为 `9 passed`，当时尚需在
这些追加修复上重新执行扩大回归和全量测试。

镜像携带链只读审查确认 `main.py`、`native_business_runtime.py`、`mcu_session.py`、
`updater_agent.py`、顶层 unit 和全部 `first_boot/systemd` 文件都有正式安装与逐文件审计入口。
可以复用 wheel、pip、uv 和 apt 下载缓存，但不能复用 v40 的签名运行时归档或整个软件载荷；
否则新源码不会进入镜像。维护安装器是工作站受控工具，其内嵌 gate 基线不作为设备运行文件
打包；镜像直接安装 `first_boot/systemd` 下的正式 unit。

追加修复完成后的扩大回归为 `324 passed, 4 skipped`。随后保持源码不变，从头重跑完整
`hardware/tests`，最终为 `4283 passed, 126 skipped, 5 subtests passed`；这才是本节最终实现的
全量结果。`tools/orangepi-image/tests/run-tests.ps1` 为 `63 tests OK, 8 skipped`，镜像输入校验、
分离签名校验均为 PASS。`git diff --check` 通过。WSL 的 `systemd-analyze verify` 未发现
hardware 与 updater 的依赖环；仅报告 Windows 挂载盘权限映射和开发机不存在目标机绝对
可执行路径，不能用这些预期提示代替新镜像上的最终 unit 审计。

修复把人工通信故障恢复的证据也收紧为“MCU 启动、固件身份和设备事实都已由前台确认为
新鲜”；仅能收到启动探测但身份尚未同步时，后台显示通信尚未恢复，不能据此解除故障。该变化
只影响人工恢复证据，不改变正常 START 或结果处理。

当前卡热部署前再次确认永久作业闸门为 `ENFORCED / LOCKED`、活动许可为 0、无活动业务、无
未核对物理动作。随后停止业务服务，备份旧运行时代码，并在 SQLite 在线备份到
`/home/orangepi/edge-pre-session-clock-latch-clear.db` 后，只对值仍精确等于
`NATIVE_RUNTIME_FAILED` 的一行执行比较更新；没有清其他故障、业务槽或历史证据。热部署后重启
服务。第一次快照隔离验证后，又把上述有效期和同代读取修复对应的
`native_business_runtime.py`、`mcu_session.py`、`main.py` 一并热部署到真实 release 目录并重启。
最终真实厂家进度为 `RUNNING / READY / CONNECTED`，本机状态为启动编号
`8000000000000002`、通信新鲜、无活动业务、无锁存原因；硬件与更新器均为 `active/running`、
最终服务进程为 PID `151447`，本轮 `NRestarts=0`。观察期间后台状态线程每 5 秒运行，并经历多批 OneNet 平台回复和
`confirmEdgeEvent` 命令；另连续执行 50 次本机故障状态查询，最终仍为
`RUNNING / READY / CONNECTED` 且无锁存原因，原时钟异常未复发。

因此本节当前状态是**生产进程、UART 和 OneNet 启动阻断已修复并通过当前卡服务重启验证**。
它仍不替代下一镜像冷启动、反向 SSH、正式封存与生产投递/清运 HIL。

### 5.5 最终源码候选的原子热替换

完整回归结束后，现场先再次读取永久作业闸门和本机运行状态。替换前没有活动投递或清运，
活动许可为 0、未核对物理动作为 0；永久闸门保持
`ENFORCED / LOCKED / STAGE4_ACTIVATION_REQUIRED`，因此替换过程不能接入新的正常业务。

旧文件备份在实机 `/root/ecobin-v40-pre-final-hotfix-20260915/`。该目录同时保存
`before.sha256` 和 `after.sha256`，没有覆盖前一次热修备份。替换过程先停止唯一 UART 所有者
`ecobin-hardware.service`，在目标文件所在文件系统写入临时文件、同步后以 `rename` 原子替换，
随后执行 `systemctl daemon-reload`，先恢复首次启动相关 gate，再启动硬件服务。没有停止、启动
或调用任何清运动作入口。

最终候选在工作站和实机安装后的 SHA-256 一致：

| 实机目标来源 | SHA-256 |
|---|---|
| `hardware/main.py` | `294d0dd48ee91a7d40ed7e283916688e50c81fa466343db537f476c00afda8b7` |
| `hardware/native_business_runtime.py` | `1411a745a34b431705d6ce11304bbac52c4c6858795125d06014e4619bd8b9f9` |
| `hardware/mcu_session.py` | `82ce14bb20a5e38396e452b992fb20807fdd41e05e964ad4367fc1c8645fc2d9` |
| `hardware/updater_agent.py` | `88afa365abc37de6ed5de415a10030a6c5972c40cf31470c94015ab849811a84` |
| `hardware/ecobin-hardware.service` | `f51266b01b7b2cb798dca66b4d2a21dd09ed7edb36caa80f5225909d22350ba4` |
| `hardware/ecobin-updater.service` | `e6361b13e94bb3927f1b8cd7602e5995edce72259800ea852e7025647b09654c` |
| `first_boot/cellular_modem.py` | `39b15031858b76fc3e077938b57ede078cb897f77b9b0330e5a25a7d5bb0ad8f` |
| `first_boot/orchestrator.py` | `cffd32205659c32e59c95a30dc7c37438b28ba3c524759fe75224522d328bb11` |
| `first_boot/state_machine.py` | `c93e710f5e096f290347652f7602e19694ddb0fbb03b2f5214aa302f6cfc2cdd` |
| `ecobin-cellular-uplink.service` | `64b9b587a93bb99ccf4890b2c6cd199d6c05ea61013b5c3acc6b696d37adb0cf` |
| `ecobin-enrollment.service.d/20-first-boot-gate.conf` | `a74ca1477152ff256748833d00e81fc7cdfce0e9633c7fd4eda27f34ae3999bd` |
| `ecobin-factory-handoff.service` | `e3da376f8add73e1cbe03e8a5ee2e0f4998747b3ab8e2c1945b2ad3b34f7e59c` |
| `ecobin-factory-test.service` | `07da591e414dcd573b4c4d1b4e5c07eb83bc4eabe16a1b5b58ba31b8c0315dd1` |
| `ecobin-hardware.service.d/20-first-boot-gate.conf` | `fab394bf59494b7030984e6402273fc47d3eaf2fe5294019c85167c2587e9e74` |
| `ecobin-remote-support.service.d/20-first-boot-gate.conf` | `a1dbf142c296b6f0054713eab4f7e5dec7b26f5962a43738adc676cfa1573189` |
| `ecobin-runtime-gate.service` | `22e313044610b75cac6017ff3fe7546e757eaeced6d03d757ebb6e374f5bc4d8` |

其中 `mcu_session.py`、`updater_agent.py`、三个 `first_boot` Python 文件、hardware gate drop-in
和 updater unit 在最终替换前已经与候选相同；本轮仍把它们纳入逐文件摘要核对，但没有为了
制造一次“修改”而重复覆盖。其余最终有差异的文件完成原子替换。

替换后即时事实为：hardware、updater、cellular 和 remote-support 四个服务均为
`active/running`；hardware PID 为 `211741`，四个服务的 `NRestarts` 均为 0。厂家公开进度为
`RUNNING / READY / CONNECTED` 且 P8 为 `IDLE`；本机故障状态没有活动业务或锁存故障，通信新鲜，
MCU 启动编号仍为 `8000000000000002`。OneNet 重新连接并完成订阅确认，日志持续出现受确认的
`DEVICE_RUNTIME_SNAPSHOT`，没有新增 traceback。永久闸门仍是锁定状态、活动许可与未核对动作
仍均为 0；这证明热替换没有私自放行接单。

随后计划执行的连续无动作查询遇到的是工作站访问竞争：`COM3` 仍由已有 MobaXterm 串口会话
独占，新的 pyserial 只读客户端返回 Windows `PermissionError(13)`。这不是设备 UART 故障，
也没有向 MCU 写入业务或动作帧；待释放工作站串口会话后从同一点继续，不得因此绕过前置状态
核对或重复发起 START。

### 5.6 周期启动探测造成的状态闪断及第二次原子热修

工作站串口释放后，从同一点重新执行 20 Hz、共 100 次的纯状态查询。修复前在第 14 次稳定捕获：

```json
{"activeWorkUid":null,"faultUid":null,"freshCommunicationConfirmed":false,"manualRecoveryEligible":false,"mcuBootId":null,"reasonCode":null}
```

紧接着的单次查询又恢复为启动编号 `8000000000000002`、通信新鲜，hardware/updater 的
`NRestarts` 均为 0，日志也没有异常。厂家公开进度的单次读取还直接捕获到
`RUNNING / STARTING / CONNECTED`。整个复现只调用 `GET_NATIVE_FAULT_STATUS`，没有发送 START、
开门、关门、解锁或任何清运动作。

根因是 `McuBootSession` 共用一个截止时间表达两个不同事实：每秒启动探测的回复窗口，以及上次
合法正启动编号的有效期。每次新探测发出前，旧代码会先清空已经确认的启动编号；在下一轮前台
轮询消费 `BOOT_PROBE_REPLY` 前，后台不可变快照因此发布一次短暂的 `STARTING`。它既不是 MCU
重启，也不是 UART 真正失联，却会令热点验收或业务 START 偶发失败。

当前源码把两种时限分开：

1. 每秒继续发送相关启动探测；
2. 最近一次由当前探测关联、持久化成功的正启动编号，在生产运行时最多保留现有的 10 秒通信
   超时；下一次探测在途不再主动制造空窗；
3. 只有相关合法正回复能够续期，期限从该次探测起点计算；仅写出探测、短写、无回复、无关帧、
   错探测号或迟到帧都不能续期；
4. 相关零启动号会立即清除旧编号并进入重新绑定；相关但不属于本 EdgeStore 的外来正编号也会
   立即清除旧编号，不能继续使用旧代或冒认新代；
5. 所有业务命令仍携带 `targetMcuBootId`，发送前和 MCU 接收端都校验目标启动代次。因此 10 秒
   的只读证据保留不会让旧代动作在重启后的 MCU 上执行；到期仍失败关闭并拒绝新业务。

新增测试覆盖同启动号续期、10 秒准确失效、周期短写不续期、相关零号和外来正号立即失效、
默认 1 秒兼容语义、参数边界，以及运行时 `GET_NATIVE_FAULT_STATUS` 在探测在途时不闪断而在
真正超时后失效。启动会话和完整原生运行时定向回归为 `59 passed`。

第二次热修前再次确认：无活动业务、无本机故障，永久更新器活动许可为 0、未核对物理动作为
0，厂家测试与交接服务均未运行。备份目录为
`/root/ecobin-v40-pre-boot-freshness-hotfix-20260915/`，其中保存旧源码、hardware unit 以及替换
前后的摘要。仅下列两个正式源码文件有变化，unit 被备份并复核但内容未变：

| 文件 | 替换前 SHA-256 | 替换后 SHA-256 |
|---|---|---|
| `mcu_session.py` | `82ce14bb20a5e38396e452b992fb20807fdd41e05e964ad4367fc1c8645fc2d9` | `57442bbfc96fe28345a325446dae74eac9a9d361ee1e9bd7c27a148b45bbd905` |
| `native_business_runtime.py` | `1411a745a34b431705d6ce11304bbac52c4c6858795125d06014e4619bd8b9f9` | `d7fb711498687486914aafd42d35190c8cc48af012030578292081623bb301df` |
| `ecobin-hardware.service` | `f51266b01b7b2cb798dca66b4d2a21dd09ed7edb36caa80f5225909d22350ba4` | 同左，未改动 |

替换使用同目录临时文件、`fsync` 和 `rename`，随后执行 `daemon-reload` 并重启 hardware。新 PID
为 `245716`；hardware、updater、cellular、remote-support 均为 `active/running`，四者
`NRestarts=0`。本机状态继续使用相同 MCU 启动编号，无活动业务、无锁存故障；厂家公开进度为
`RUNNING / READY / CONNECTED / P8 IDLE`，最近日志持续收到 OneNet 事件回复且 warning 以上为空。

修复后重新运行同样的 5 秒、100 次、20 Hz 无动作查询，100 次全部保持相同启动编号、通信新鲜、
无活动业务和无故障，覆盖了五个周期探测边界。生产 EdgeStore 和永久更新器数据库的只读基线
显示：业务槽为 `NONE`，正常投递 START、原生结果、结果上报任务、`DELIVERY_COMPLETE` 均为 0，
无未决原生命令、无观察中故障、无作业许可、无永久物理动作。当前只完成无动作现场验证；真实
投递 HIL 的结果必须另行记录，且绝不能据此宣称清运已经实机通过。

在上述源码不再变化后，从头执行最终全量 `hardware/tests`，结果为
`4294 passed, 126 skipped, 5 subtests passed`，耗时 844.26 秒；跳过项仍是当前 Windows 环境
不能执行的 POSIX/Linux 条件，不是失败。镜像工具回归为 `63 tests OK, 8 skipped`，并同时得到
`image-input-validation=PASS`、`detached-signature=PASS` 和
`ecobin-orangepi-image-tests=PASS`。这组结果取代本文件前面在追加启动探测修复前记录的
`4283 passed`，作为本次实机投递 HIL 之前的最终源码回归基线。

### 5.7 投递 HIL 前发现的瞬时称重误报

准备发起唯一一次投递 HIL 前，最后一轮生产数据库只读门禁检查在 20:26:48 捕获一条新的
`WEIGHT_SENSOR / BLOCK_PORT` 观察中故障。虽然通信状态仍显示相同 MCU 启动编号和通信新鲜，仍
按现场规则停止在 START 之前，没有发出投递或清运动作。数百毫秒后的复查显示该故障已自动
恢复；原始明细为：

```json
{"profile":"native-scale-read-v1","mcuBootId":8000000000000002,"capturedUptimeMs":22191550,"attemptSequence":87061,"readStatus":"VALID"}
```

恢复证据是同启动代次的新样本 `NATIVE_VALID_SCALE:8000000000000002:87064:22192300`。20:30:11
又记录到同类、同样立即恢复的第二条历史事实，证明它不是一次性数据库残留。

根因不是 RS485 明确读失败：MCU 已报告 `scaleReadStatus=VALID`，但该有效读数在一次前台调度
延迟中超过 750 ms 的业务新鲜度。业务准入正确地把它当作“当前没有足够新的重量”并失败关闭，
然而 `_scale_health_poll()` 只给启动时的 `NOT_OBSERVED` 五秒等待，对“VALID 但暂时过旧”立即
创建设备故障；下一个 250 ms 采集周期的新样本又马上恢复，于是产生页面和后台故障闪烁。

当前修复不放宽业务准入：只要重量不够新，当下仍不能 START。它只把 `VALID` 但过旧与初始
`NOT_OBSERVED` 一样纳入连续五秒观察；五秒内取得更新样本则不产生设备故障，连续五秒仍无可用
新样本才登记真实的陈旧称重故障。MCU 明确返回 `UNAVAILABLE`、`TIMEOUT` 等读取失败时仍立即
登记，业务内五秒测量失败、失败上报和后续新样本恢复规则均未改变。

新增测试先在旧实现上稳定得到 2 个预期失败，修复后称重失败专项为 `8 passed`；启动会话、
原生业务和称重组合为 `67 passed`。第二次称重热修备份位于
`/root/ecobin-v40-pre-scale-stale-hotfix-20260915/`。`native_business_runtime.py` 从
`d7fb711498687486914aafd42d35190c8cc48af012030578292081623bb301df` 原子替换为
`a006ec8337f54fb145b6b5937ec85691543faf8985ca075e9a1f05a7f1b7383b`；hardware unit 摘要仍为
`f51266b01b7b2cb798dca66b4d2a21dd09ed7edb36caa80f5225909d22350ba4`。`daemon-reload` 和服务
重启后 hardware PID 为 `278759`、`NRestarts=0`，再次 100 次、20 Hz 无动作查询全部通过，且
当前没有观察中称重故障。随后必须以这一最终源码重新跑全量回归，再决定是否进入投递 HIL。

### 5.8 最终回归、唯一一次隔离投递 HIL 与服务监督异常

称重瞬时误报修复后，从头运行完整 `hardware/tests`，最终结果为
`4296 passed, 126 skipped, 5 subtests passed`，耗时 845.69 秒、退出码 0。随后镜像工具回归为
`63 tests OK, 8 skipped`，并同时得到 `image-input-validation=PASS`、
`detached-signature=PASS` 和 `ecobin-orangepi-image-tests=PASS`。`git diff --check` 通过。

现场动作前先完成两次各 5 秒、100 次、20 Hz 的本机只读查询。两次都只看到同一个 MCU 启动
编号 `8000000000000002`，通信新鲜，无活动业务和锁存故障。生产 EdgeStore 的业务槽为 `NONE`；
正常投递 START、原生结果、结果上报任务和 `DELIVERY_COMPLETE` 均为 0；无未决 MCU 命令和
观察中故障。永久更新器为 `ENFORCED / LOCKED / STAGE4_ACTIVATION_REQUIRED`，活动许可和未核对
物理动作均为 0。厂家公开进度为 `RUNNING / READY / CONNECTED / P8 IDLE`，OneNet 持续收到
平台回复，动作前 30 分钟没有 warning。此时没有清运动作，也没有消耗投递 START。

最终备份目录为 `/root/ecobin-v40-pre-verified-final-hotfix-20260915/`，其中保留完整原路径文件、
`before.sha256` 和 `after.sha256`。再次停止服务、原子替换、`daemon-reload` 并重启相关服务后，
16 个文件的替换前后摘要完全一致，证明当前卡已经精确运行本次全量回归候选：

| 实机目标 | 替换前及替换后 SHA-256 |
|---|---|
| `hardware/current/app/main.py` | `294d0dd48ee91a7d40ed7e283916688e50c81fa466343db537f476c00afda8b7` |
| `hardware/current/app/mcu_session.py` | `57442bbfc96fe28345a325446dae74eac9a9d361ee1e9bd7c27a148b45bbd905` |
| `hardware/current/app/native_business_runtime.py` | `a006ec8337f54fb145b6b5937ec85691543faf8985ca075e9a1f05a7f1b7383b` |
| `updater/current/app/updater_agent.py` | `88afa365abc37de6ed5de415a10030a6c5972c40cf31470c94015ab849811a84` |
| `factory-test/current/app/first_boot/cellular_modem.py` | `39b15031858b76fc3e077938b57ede078cb897f77b9b0330e5a25a7d5bb0ad8f` |
| `factory-test/current/app/first_boot/orchestrator.py` | `cffd32205659c32e59c95a30dc7c37438b28ba3c524759fe75224522d328bb11` |
| `factory-test/current/app/first_boot/state_machine.py` | `c93e710f5e096f290347652f7602e19694ddb0fbb03b2f5214aa302f6cfc2cdd` |
| `ecobin-hardware.service` | `f51266b01b7b2cb798dca66b4d2a21dd09ed7edb36caa80f5225909d22350ba4` |
| `ecobin-updater.service` | `e6361b13e94bb3927f1b8cd7602e5995edce72259800ea852e7025647b09654c` |
| `ecobin-cellular-uplink.service` | `64b9b587a93bb99ccf4890b2c6cd199d6c05ea61013b5c3acc6b696d37adb0cf` |
| `ecobin-enrollment.service.d/20-first-boot-gate.conf` | `a74ca1477152ff256748833d00e81fc7cdfce0e9633c7fd4eda27f34ae3999bd` |
| `ecobin-factory-handoff.service` | `e3da376f8add73e1cbe03e8a5ee2e0f4998747b3ab8e2c1945b2ad3b34f7e59c` |
| `ecobin-factory-test.service` | `07da591e414dcd573b4c4d1b4e5c07eb83bc4eabe16a1b5b58ba31b8c0315dd1` |
| `ecobin-hardware.service.d/20-first-boot-gate.conf` | `fab394bf59494b7030984e6402273fc47d3eaf2fe5294019c85167c2587e9e74` |
| `ecobin-remote-support.service.d/20-first-boot-gate.conf` | `a1dbf142c296b6f0054713eab4f7e5dec7b26f5962a43738adc676cfa1573189` |
| `ecobin-runtime-gate.service` | `22e313044610b75cac6017ff3fe7546e757eaeced6d03d757ebb6e374f5bc4d8` |

一次性投递工具在实机执行前又经过安全审查和 3 项专门测试。最终工具摘要为
`29733054ef0b1d68417b94dc71c1991d8dcf71d0ac4af69e39d8dfb725192ccc`。它固定只允许
`DELIVERY`，没有清运、清运解锁或 `BIND_BOOT` 路径；MCU 启动号为零或改变时立即失败；START
先持久记录“已尝试写出”，随后最多写一次，丢回复只查询；完整结果、门控事实和启动编号必须在
发送 `RESULT_SAVED` 前全部通过。进程还以 `PrivateNetwork=yes`、`IPAddressDeny=any`、仅
`AF_UNIX`、仅放行 `/dev/ttyS5`、生产 EdgeStore/更新器/设备配置不可见的 transient systemd
沙箱运行，因此不连接 OneNet、不读写生产业务库，也没有正常订单、余额或提现价值。

项目负责人已明确确认投递门关闭、机构无卡物且运动范围无人，只允许一次真实投递。该唯一一次
START 的结果为 PASS：

- `mcuCommandUid=4358930f-c46c-4bc4-b969-381666dd88ee`，
  `workUid=cde25cbb-6593-43c1-b3fd-449c9239e68c`，命令序号 19；
- MCU 启动编号全过程保持 `8000000000000002`，固件为 `1.0.2-hil.6`；
- 初重 0 g、末重 5 g，均为 5 个样本的 `STABLE_MEAN`，两组跨度均为 0 g；
- 结果序号 3、摘要
  `9c296b7d9d4cc50939f71ed0f96ff45e80172ef202dedee8e476f7cc8bbadd62`，终态原因是
  `DELIVERY_WINDOW_EXPIRED`，投递轮数为 1，清运动作序号为 0；
- 释放前设备事实为 `lastDeliveryDoorCommand=CLOSE`、`PB6=0`、`PB7=1`、
  `cleanLockPowered=false`，符合已经确认的“最近有效推杆输出即门状态”模型；投递没有独立门位
  反馈，因此这证明关门控制持续生效，不额外声称存在物理门位传感器；
- 满溢来源为超声波但本次是 `UNAVAILABLE`，按已确认边界只保留辅助告警、不阻断投递；烟感为
  `NORMAL`，称重为 `VALID`；
- 结果先通过完整校验，之后才发送 `RESULT_SAVED`，最终 MCU 确认释放。日志中的
  `workResultCount=2` 是同一个结果在主动交接/查询中的两次帧观察；持久活动只有一个 workUid、
  一个结果序号和一个摘要，不代表执行了两次投递。

隔离 UART 日志为 3382 字节、SHA-256
`5238a954249196bf791102206d1dc7b2662fb0a25a1ad591f17a8f8e76c6fe20`；最终证据为 11234 字节、
SHA-256 `60710f7c8162df0735e15984bd11e018617c92f2425569ffa13391be62a40e88`，两者均在实机
`/var/lib/ecobin/hil/v40-delivery-20260915/` 下以 0600 保存。HIL 后生产库再次确认投递 START、
结果、上报任务、完成事件仍全部为 0，业务槽仍为 `NONE`，永久许可和动作账本仍为空；这证明
隔离结果没有被当作生产业务或进入资金链。它验证了 MCU 本地投递、称重、结果保存/释放和本地
槽不占用，不替代生产 OneNet 投递上报闭环。

本次还暴露了一个测试隔离流程缺口：常驻的 `ecobin-first-boot.service` 会按设计持续确保正式
运行时成员在线。HIL 包装器只停止了 `ecobin-hardware.service`，没有先停止协调器或建立既有
`runtime-start-blocked` 启动围栏，因此协调器在 HIL 独占 UART 期间重新拉起硬件服务。正式
服务在打开 `/dev/ttyS5` 时失败，留下 15 次 `Could not exclusively lock port` 的重启证据；
它在取得 UART 之前就退出，不能发送业务 START。HIL 释放串口后，正式服务于 21:01:37 UTC
自动恢复，PID `310298`，`NRestarts=15` 此后不再增加；21:02 UTC 后无 warning，OneNet 重新
连接并完成订阅，厂家状态回到 `RUNNING / READY / CONNECTED / P8 IDLE`，又一轮 100 次状态
查询全部通过。生产库和永久账本前后不变进一步证明这 15 次启动失败没有形成业务或动作。

原始重启日志和计数保留，不执行第二次投递来“修饰”结果。以后任何独占 UART 的厂家/HIL 工具
必须在停硬件服务前先使用正式运行时启动围栏，或停止首次启动协调器并在退出后恢复；仅停止
`ecobin-hardware.service` 不是充分隔离。该缺口属于现场测试编排，不改变生产协调器主动恢复
正式服务的正确职责。

本轮没有发起清运、没有打开清运门、没有解锁清运机构、没有发送任何清运动作指令。清运只由
自动化测试和协议测试覆盖，真实清运 HIL 仍明确未完成；不得用上述投递结果替代清运通过。

### 5.9 v41 可复现镜像构建、敏感材料隔离与最终在线复核

现场验证完成且源码不再变化后，先提交最终运行时源码：
`0650dbe89e1232916709be8ed3cb3f4c07ef91e9`（`fix(hardware): stabilize v40 native runtime`）。
该提交包含本轮运行时、unit、测试和前述过程记录；明确没有包含项目负责人保留的
`hardware_mcu/USER/STM32-DEMO.uvprojx` 与 `hardware_mcu.zip`。用于镜像构建的离线 Git bundle
为 `hardware/image-artifacts/local/source-bundles/repository-v41-20260915.bundle`，SHA-256 为
`2cae5cc925d0618f2fde4ad46312d18591f9d471526964f5f1f2d198a9a42ebf`；`git bundle verify`
通过，bundle 中唯一 HEAD 精确指向上述提交并包含完整历史。

v41 构建锁定值如下：

| 输入 | SHA-256 |
|---|---|
| `uv.lock` | `9d42ea99614e3763c739887b29e2c3c9bfb6293e014706b525fc7db3e9bb27c8` |
| 镜像构建器输入 | `da6b71eed4f3bc0d9563883e750c760c79da7864016f04edcd0b74f9bf79d5aa` |
| APT 输入 | `65969460a6f4a9c05ed56c53f1d054dda689e6496fd1878daa5f4000997aa53c` |
| 源码输入 | `3b6a72a2bc2870d713c0835cab8d9c5bc8b8809f1952c2b65c39a19dc6eb605f` |
| 镜像布局输入 | `3bc5ca7191bb88b813edb66f7677660aa16f38e117f8dfaeeba80f2fa1fb4389` |

仅复用依赖下载缓存 `/var/cache/ecobin-image-builder/arm64-py311-9d42ea99614e3763` 和固定基础镜像；
没有复用 v40 运行时归档或 v40 软件载荷。分别在
`/var/lib/ecobin-image-factory/hil-v41-20260915-repro1` 与
`/var/lib/ecobin-image-factory/hil-v41-20260915-repro2` 从提交源码独立生成两次候选，结果逐字节一致：

| 产物 | 两次构建共同 SHA-256 |
|---|---|
| 新运行时归档 | `dba4d188cd5fab25f087e2e6975aaf9fb8373fefe4f0e7bf32afcf578b18f3ac` |
| 新软件载荷锁 | `8adb90b3151a9d108e32eab521f672d981f609f2e3c046ac16e77981842a3093` |
| 2,571,108,352 字节未注入厂家材料的候选镜像 | `319e4106849795510acfe9b794cbb7cfe7c8e5c94b7e68af3146fd1bac0f3f02` |

候选清单标记为 `UNSIGNED_NO_SECRET_CANDIDATE`，软件版本为
`0.1.0-single-card.20260915.41`，release 为 `single-card-hil-20260915-41`，运行时为
`hardware-runtime-20260915-41`，`sourceDirty=false`。作为对照，v40 的运行时归档、软件载荷锁和
候选镜像摘要分别为
`b1528daf53f8cd588aac9a105f0a699b638c88cb4519d7d45e7948a5d590fde4`、
`5456fb02a6e01ae3644452cd4b5ebb533102bd226d30d983ac95f411b137d614`、
`94e1bf8314310379c579ea469ae742949b7c121fefd8c6455ab0ca9869f2b84d`，均与 v41 不同。

构建过程中保留了两个编排问题及其修复，避免下次把包装器故障误认为镜像主体故障：

1. 第一次候选的构建主体已经通过，但执行中的包装脚本被并发编辑，退出时触发 shell 语法错误，
   因而该次包装器退出码为 1。交换分区仍已恢复，之后不再在脚本运行中修改同一文件，并用第二次
   独立构建证明产物一致。
2. 第二次候选主体通过后，包装器误报 `/dev/sdc swap not restored`。根因是 util-linux 2.39.3
   下 `swapon --show --output NAME` 的输出格式被错误解析，实际交换分区已恢复。检查改为
   `swapon --show=NAME --noheadings --raw`，并增加“操作前记录意图、退出时按实际状态恢复、恢复后
   强制断言”；随后确认 `/dev/sdc` 处于活动状态。

准备注入厂家材料时又发现，原 `factory-secret` 位于 DrvFS，Windows ACL 曾允许普通 Users 读取、
Authenticated Users 修改，不能作为敏感构建目录。第一次最终镜像尝试在 `copy-candidate` 刚开始
时即被中断，早于任何密钥注入；不完整目录被隔离为
`hardware/image-artifacts/local/factory-secret/builds/aborted-before-secret-injection-20260915-41`，
其中只有候选副本和 34 字节开始日志，没有摘要文件或密钥，明确不是有效成品。ACL 第一次收紧时
把目录继承参数错误应用到普通文件，曾使文件直接 ACL 为空并导致本账户也无法读取；随即按“目录
设置继承权限、每个普通文件设置直接权限”修复。最终整个 `factory-secret` 树仅当前 Windows
账户、`SYSTEM` 和 `Administrators` 具有 Full Control；v40 受保护来源仍可读取，且摘要保持
`21581060db890b0902e2cc270e44b23e1867f6e7bdd8db8eeac44948b258690a`。

最终厂家/HIL 镜像改在真正的 WSL ext4 私有目录
`/var/lib/ecobin-image-factory/private/hil-factory-login-20260915-41-uart2-rc26` 中构建；目录为
root:root 0700，镜像为 root:root 0600。整个 v41 候选镜像是唯一软件基底；v40 受保护镜像只提取
`enrollment.key`、`setup-ap.key` 和 `orangepi` 密码散列字段，没有继承旧运行时、unit、数据库、
设备身份、OneNet 凭证、SSH 主机密钥或网络运行状态。构建及离线检查全部通过：原始分区布局、
`e2fsck`、ext4 安全属性、镜像软件清单、root 账户锁定、厂家登录、受保护材料同源、干净首次启动、
摄像头规则和源码提交均符合要求。

最终成品为：

- 文件：`ecobin-orangepi-zero3-0.1.0-single-card.20260915.41-hil-factory-login.img`；
- 大小：2,571,108,352 字节；
- SHA-256：`297a400aa5d15423139ac084ba5e05cc075520c871a7a716048b4da54ad6aff6`。

对候选与最终 HIL 镜像做全文件树差异审计，共 53,461 个条目保持一致；变化仅限预先批准的三个
厂家材料路径，启动分区前缀继续继承 v41 候选。账户散列按来源值和元数据精确比较但不输出内容，
两个密钥均确认是 0600 普通文件且与受保护来源一致。差异审计日志为 277 字节、SHA-256
`5a7b5cbfb564fba0ddede3e8054033e056d089b906f1fee5127458d2ed7bdf75`。

ARM64 离线冒烟测试第一次虽然通过，但检查输出暴露包装器仍调用 v35 的旧辅助脚本，因此不计入
最终证据。修正为 v41 专用辅助脚本后重新运行并通过：Python 3.11.2、UART `/dev/ttyS5` 115200
及 uart-v2、更新器隔离/重启与 `updater-20260915-41` release、8 组厂家报告样例、通信模块导入、
rc.26 共 73 种消息及能力掩码、EdgeStore schema 40、OneNet 2.4、二维码入口、28/400/500/1000 g
参考重量和报告校验均正常。测试使用只读镜像、合成状态且完全禁网，没有访问真实硬件。最终冒烟
日志为 982 字节、SHA-256
`1c9c03f70a26df1026b7945b79b1634f6db297d369adde27d3102b3885c374d5`；构建及离线验证日志为
1,392 字节、SHA-256
`ffe2bace459576ae051e057c1fd164cea0ca2ef01f62e181ad9ffb703b0a0e45`。

成品及摘要、构建日志、冒烟日志、差异日志已经复制回 Windows 受限目录
`hardware/image-artifacts/local/factory-secret/builds/hil-factory-login-20260915-41-uart2-rc26`。
Windows 对整幅 2.57 GB 镜像和三个日志做完整回读，摘要与 ext4 私有构建区一致；目标目录和每个
文件的显式 ACL 也只包含当前账户、`SYSTEM` 和 `Administrators`。该镜像目前只是已验证待写卡
产物：本轮没有插拔或写入 TF 卡，也没有把 v41 加入后台认可列表；这两项仍需项目负责人明确
授权后单独执行，不能把离线镜像通过当作写卡回读或新卡验收通过。

最后于设备时间 2026-09-14 22:11 UTC 再通过串口做只读在线复核。`ecobin-hardware.service` 仍为
PID `310298`、`active/running`，启动时间仍是 21:01:35 UTC，累计 `NRestarts=15` 没有增加；
updater、cellular-uplink、remote-support 也均为 `active/running` 且没有累计重启。最近 90 秒每
5 秒持续发布设备运行快照并收到 OneNet 回执。warning 查询只出现两条本次 `sudo -n true` 因无
缓存密码而被拒的只读探测记录，没有设备应用 warning；因此没有为最后核对再次提权、替换、重启
或发起业务。普通 `orangepi` 用户因运行目录权限不能列出控制 socket，这个结果不作为 socket
不存在的判断；其前面的受权数据库、控制端点和 100 次连续查询证据仍是最终业务状态依据。

本节镜像构建和末次复核都没有再次发送投递 START，也没有发出任何清运、清运门或清运解锁
指令。真实清运 HIL 仍未执行。

### 5.10 v41 写卡及完整回读

项目负责人插入目标 TF 卡并明确回复“确认覆盖磁盘 1”后，先以只读方式重新识别 Windows 磁盘：
磁盘 0 是 1,024,209,543,168 字节的系统 NVMe；唯一外接目标是磁盘 1，设备名
`Mass Storage Device`，USB，总容量 31,268,536,320 字节，序列号 `121220160204`，状态
`Online / Healthy`，且 `IsBoot=false`、`IsSystem=false`。写卡脚本同时固定磁盘编号、序列号、
容量、写卡器脚本摘要、镜像字节数和镜像摘要，任一项不符都会在打开物理盘写入前失败关闭。

写卡器 `tools/orangepi-image/Write-HilImageToDisk.ps1` 的复核摘要为
`2117d2e231ae001187e04c48c64e56b4fca5a21d99ec308777d7e015e69c9d78`。本次专用脚本为 Git
忽略的现场工具 `hardware/image-artifacts/local/flash-v41-hil-disk1.ps1`，语法检查通过，摘要为
`3bd2cb68c8607f4afa165e937ad97b9d265df3ea59c261de68eae34ae62f6448`；它使用 UAC 取得物理盘
写权限，不接收模糊磁盘选择，也不覆盖既有写卡证据。

2026-09-15 02:35:15 UTC 开始校验，02:35:23 UTC 完成目标和镜像门禁，随后向磁盘 1 写入
2,571,108,352 字节。02:37:52 UTC 写入完成并 flush；02:37:53 UTC 开始从 TF 卡同一范围完整
回读，02:40:07 UTC 完成。结果为：

| 项目 | 结果 |
|---|---|
| 源镜像 SHA-256 | `297a400aa5d15423139ac084ba5e05cc075520c871a7a716048b4da54ad6aff6` |
| 实际写入字节数 | 2,571,108,352 |
| 完整回读字节数 | 2,571,108,352 |
| TF 卡回读 SHA-256 | `297a400aa5d15423139ac084ba5e05cc075520c871a7a716048b4da54ad6aff6` |
| root 密码状态 | locked |
| 最终结果/退出码 | `PASS` / `0` |

写卡后再次读取磁盘身份，仍是磁盘 1、序列号 `121220160204`、31,268,536,320 字节、
`Online / Healthy`，没有被替换或掉线。Windows 将镜像中的第一分区显示为 `Unknown` 是因为该
分区为 Linux ext4，不是写卡失败；剩余卡容量由镜像中的首次启动扩容服务在目标机冷启动时处理。

永久证据及其 SHA-256 为：

| 证据 | SHA-256 |
|---|---|
| `flash-v41-hil-disk1.progress.log` | `265fdfa782cd9081b3f76cb88d136507e399cdb89a3b5755eccaf43973192cec` |
| `flash-v41-hil-disk1.result.json` | `337cb560116731b8386427c1239dd3c1db82d52f0075c4c68f0f66fb178fe534` |
| `flash-v41-hil-disk1.exit-code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` |

本步骤只完成 TF 卡物理写入和逐字节回读，没有启动新卡、没有伪造厂家验收、没有发起投递或
清运，也没有修改后台版本认可列表。v41 是否被后台接受仍需在实际厂家验收前单独确认；写卡
PASS 不能替代新卡冷启动、热点、UART、RS485、OneNet、反向 SSH 或业务现场验收。

## 6. 当前卡实际热修改清单

重新烧卡会丢失下列现场修改：

| 目标 | 当前卡状态 | 新镜像来源 |
|---|---|---|
| 厂家验收 `acceptance_service.py`、`acceptance_hardware.py`、`native_acceptance_mcu.py` | 已热修 | 提交 `780d2ecb`、`5bf89e67` |
| 厂家封存 `validation.py`、`weight_validation.py` | 已热修 | 提交 `780d2ecb` |
| 热点页面 `hardware/factory/web/app.js` | 已热修 | 提交 `780d2ecb` |
| 厂家运行目录 `first_boot/state_machine.py` | 已热修 | 本次已验证候选 |
| 厂家运行目录 `first_boot/orchestrator.py` | 已热修 | 本次已验证候选 |
| 厂家运行目录 `first_boot/cellular_modem.py` | 已热修 | 本次已验证候选 |
| 硬件服务 `20-first-boot-gate.conf` | 已热修并 `daemon-reload` | 本次已验证候选 |
| 其他六个首次启动 gate unit | 已热修并完成最终摘要复核 | 本次已验证候选 |
| 维护安装器内嵌 gate 正文与摘要 | 不是设备运行文件；仓库已改且摘要测试通过 | 本次已验证候选 |
| 旧更新器永久作业闸门参数 `ecobin-updater.service` | 当前卡已热修 | 本次已验证候选 |
| 旧业务服务永久许可参数 `ecobin-hardware.service` | 当前卡已热修 | 本次已验证候选 |
| 原生 systemd 就绪状态、异常栈及同代快照消费 `hardware/main.py` | 当前卡已热修并用于取得精确堆栈 | 本次已验证候选 |
| 更新器 root 迁移姿态 `hardware/updater_agent.py` | 当前卡已热修 | 本次已验证候选 |
| 原生后台只读快照隔离、同代消费及启动探测间隙 `hardware/native_business_runtime.py` | 当前卡已热修，最终 HIL 后再次 100 次 20 Hz 查询通过 | 本次已验证候选 |
| MCU 启动编号、独立证明截止时间及严格失效规则 `hardware/mcu_session.py` | 当前卡已热修，周期探测实机验证通过 | 本次已验证候选 |

`hardware/image-artifacts/local/` 下的串口上传、诊断和热修复脚本是 Git 忽略的现场工具，不是镜像
源码。新镜像不能依赖这些脚本存在，也不能因为脚本执行成功就漏掉上表中的正式源码和 unit。

## 7. 下一镜像构建前的强制核对

只有以下各项全部满足，才进入递增版本镜像构建：

1. `git status` 只保留已知用户文件和本次准备提交的变更；不覆盖
   `hardware_mcu/USER/STM32-DEMO.uvprojx` 与 `hardware_mcu.zip`。
2. 新提交包含 `780d2ecb`、`5bf89e67`，以及第 4 节全部源码、unit、维护安装器内嵌基线和
   测试；同时提交本文、`CLAUDE.md` 的入口和前一日热点修复记录的后续完成说明，不能让
   防遗漏清单或验收状态消歧只留在未跟踪工作区。
3. 第 5 节启动阻断在仓库形成单一实现；必须保留本次完整自动化回归、当前卡热部署、OneNet/
   UART 所有权及隔离 HIL 证据，不能只引用早期专项结果。
4. 新提交若再改变运行时代码，必须重新运行完整 `hardware/` 测试；专项通过不能替代全量结果。
5. 使用既有依赖缓存构建，不重新下载能复用的依赖；新版本号、release ID、payload lock 和镜像
   清单必须一致。
6. HIL 镜像包含所需厂家接入/热点材料，但不继承当前卡的设备身份、OneNet 凭证、业务库、
   SSH 主机密钥或网络运行状态。
7. 后台只在镜像离线审计通过后追加认可的新硬件运行时版本；认可不替代写卡回读和真机验收。

## 8. 新卡必须复跑的现场验收

1. 完整写卡并对镜像范围做 SHA-256 回读。
2. 冷启动后热点可见，点击控制板和传感器检查时 MCU 和 RS485 确实收发。
3. 350 kg 秤允许稳定负零点，页面展示空载、加载、卸载实际值和差值；已知参考重量仍可填写。
4. 超声波/满溢或其他辅助事实不可用时明确提示但不阻断；称重读不到仍阻断。
5. 拔 SIM 时显示 `CELLULAR_SIM_ABSENT`；重新装好后恢复 `CPIN READY`、注册、附着和 DHCP。
6. 完成注册后短时阻断 HTTPS，确认本地运行时仍为 active、新业务被拒绝、恢复网络后自动连接。
7. 未封存阶段确认 OneNet 真正在线并能接收封存授权；不能只看凭证文件存在。
8. 封存后冷启动，确认只有一套业务链拥有 UART/OneNet，硬件/业务服务不会循环退出。
9. 通过后台开启一次有期限的反向 SSH 授权并实际连接；授权仍有效时重启远程维护服务，确认
   能用同一授权自动重连；最后从后台关闭授权，并确认设备不再重连。不能只看
   `ecobin-remote-support.service` 为 active。
10. 核对首次独立业务库中的当前袋、空袋皮重和基线来源；不得把厂家验收动作或参考重量当成
    生产袋状态。
11. 真实串口屏二维码、投递、清运、结果保存确认和照片链复测。
12. 断电重启只按已确认规则处理，不把工厂验收动作生成正常订单、余额或提现。

任何一项失败，都把实机原始错误、服务状态和对应源码修复追加到本文，再构建下一版；不要只在
当前卡上继续覆盖文件。
