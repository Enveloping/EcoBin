# 原生运行入口：控制通信失败收口

日期：2026-09-13。依据[精简实施计划](../../../docs/planning/mcu-edge-simplified-implementation-plan-2026-09-13.md)
和[D05、D06、D10决定](../../../docs/architecture/mcu-edge-simplified-recovery-discussion-2026-09-13.md)。
本批只修改本地代码、文档和测试；未连接设备、未部署后端、未烧录MCU/HMI，也未写TF卡。

## 已实现的判断

- 整条UART链路连续超过既有`communication_timeout_ms`无有效回复时，判为控制通信不可用；默认值保持10秒。
- 原始`START_DELIVERY_SESSION`（开始投递）或`START_CLEAN_OPERATION`（开始清运）已经登记为可能写出，
  但其决定回复和按原编号查询的回复连续超过同一期限仍未取得时，也判为控制通信不可用。
  其他`DEVICE_FACTS`（设备事实）回复只能证明UART整体仍通，不能再掩盖这条开始指令没有回应。
- 通信超时仍不等于MCU重启。只有保存了更大的真实MCU启动号，才进入“MCU重启、缺最终结果包”的问题归档。
- 超时边界先接收和保存本轮已经到达的完整最终包；最终包已可靠落盘时，完整结果路径优先，不被通信失败覆盖。

## 原业务如何收口

Pi先在一个SQLite事务中完成四件事：把原云端命令记为`FAILED`（失败）、建立唯一的
`DEVICE_COMMAND_OBSERVED`（设备命令观察）事件、保存`nativeControlFailure`失败凭据，并把原工作槽置为
`COMPLETING`（正在收尾）。此时尚不释放原占用，也不修改袋、皮重、重量或任何资金记录。

随后按原永久作业许可的真实状态收口：

| 串口写入事实 | 永久许可状态 | 处理 |
| --- | --- | --- |
| 明确从未登记写出 | 不存在 | 不制造许可；核对“不存在”后应用本地失败 |
| 明确从未登记写出 | `GRANTED`（已授权、未开始） | 用原命令编号和失败证据摘要将许可记为`ABANDONED`（放弃） |
| 明确从未登记写出 | `ACTIVE`（永久层已开始、串口仍未写） | 用原命令编号和同一摘要记为`COMPLETED/FAILED` |
| 已登记可能写出 | `ACTIVE` | 用原命令编号和同一摘要记为`COMPLETED/FAILED` |

只有再次读取并核对准确的永久回执后，Pi才释放匹配的原本地槽。Pi若在永久提交后、本地释放前崩溃，
重启后继续读取同一凭据和回执，不重复开始业务，也不改用新的编号。

已经登记可能写出的通信失败使用`FAILED / MCU_COMMUNICATION_UNAVAILABLE`；能由SQLite写入栅栏证明从未可能写出的，
使用`PRE_START_FAILED`（开始前失败）。两者都不会生成正常投递/清运完成事件，不产生正常订单、余额或提现。
失败后到达的完整结果仍保存原始字节和分类待办，但没有原工作槽，不会自动恢复正常上报或结算。

## Pi单独重启的精确边界

D06“Pi单独重启继续处理原业务”仍适用于已经登记可能写出的原开始指令：Pi继续查询同一业务，不重发开始命令，
也不因自身重启直接判失败。

若原开始指令的SQLite写入栅栏明确为“未写出”，MCU端实际上还没有开始该业务。工作槽现在保存创建它的Pi运行实例编号；
新实例发现这类遗留项后不重发开始，而是明确记为`PRE_START_FAILED / EDGE_RESTARTED_BEFORE_START`并释放。
这不是取消MCU中正在执行的业务，而是结束一个从未越过串口写入边界的本地准备项。当前进程正常等待永久授权时实例编号不变，
不会被这条规则误取消。

## 持续故障和人工处理

真实通信超时会保留`native_blocking_fault=MCU_COMMUNICATION_UNAVAILABLE`，拒绝后续新业务；不会因后来偶然收到一帧数据自动清除。
同时复用现有故障链记录`UART / UART_PROTOCOL / BLOCK_DEVICE`，并在明细中写入
`native-control-communication-v1`、准确原因和`automaticRecovery=false`，供后台和页面显示。
若此前已有其他阻断原因，本批不会用通信原因覆盖它；UART故障仍作为独立故障事实记录。

本批不自动解除通信故障。后续增量已经接入只允许本机 `root` 使用的人工核查/解除入口；具体条件和验证见下节。

## 人工确认恢复增量

现场人员排除通信故障后，先读取当前故障状态，再用返回的准确 `faultUid`（故障编号）执行恢复。入口同时要求：

- 操作人明确提交“原因已经排除”和非空处理说明；
- 当前没有仍占用本地工作槽的投递或清运；
- Pi刚收到过当前MCU启动号下的真实 `DEVICE_FACTS`（设备事实）回复，且仍在既有通信超时时限内；
- 待恢复的故障仍是同一个设备级 `UART_PROTOCOL / MCU_COMMUNICATION_UNAVAILABLE` 故障，未被更新一故障替代。

故障转为 `RECOVERED`（已恢复）、新增 `DEVICE_FAULT_RECOVERED` 事件和清除本地通信阻断锁在同一个SQLite事务完成；
任一步失败都会整体回滚。旧页面、随机收到的一帧或过期的重复操作都不能解除新故障。解除后只允许系统重新执行普通接单检查，
不会绕过称重、配置、其他控制故障或业务互斥，也不会恢复已经失败的原业务。

正式业务发布目录中可按以下方式操作；实际发布可能位于兼容的 `hardware/current` 路径，先以服务当前使用的目录为准：

```bash
sudo /opt/ecobin/business/current/.venv/bin/python \
  /opt/ecobin/business/current/app/native_fault_control_cli.py status
sudo /opt/ecobin/business/current/.venv/bin/python \
  /opt/ecobin/business/current/app/native_fault_control_cli.py recover \
  --fault-uid <status返回的faultUid> \
  --reason "已检查串口接线和MCU供电，通信恢复" \
  --confirm-cause-fixed
```

相关Python定向集合84项通过，包含只允许UID 0、精确故障编号、新鲜MCU事实、活动业务拒绝、并发故障变化以及事件写入失败时事务回滚；
Java 21发布包固定清单5项通过。发布清单同时补入此前S1/S2的14个运行文件，业务应用清单为53个文件、schema 25，
Python 3.11权威摘要为 `fab9540f7707814996dd97b9bca9e83659350f4228ff792444e06ee5ea35e550`。
这些均是本地候选验证；未连接现场设备、未部署或执行恢复命令。

## 验证

- 新增5项真实纵向测试：编译后的实际C执行器、真实SQLite与真实UpdaterStore共同运行。
- 覆盖“设备事实持续正常但原START回复全丢”、失败后拒绝新业务、迟到完整结果仅保存、未写START对应永久许可
  `不存在/GRANTED/ACTIVE`三类收口，以及永久失败已提交而本地应用前Pi崩溃。
- 与正常原生业务、MCU重启问题、配置重装、末重失败和永久账本异步测试统一运行：**151项通过，78.79秒**。
  JUnit位于本机临时目录`ecobin-native-control-failure-s2.xml`。
- 后端既有投递/清运命令观察决策定向测试**11项通过**：开始前失败结束未开始的占用；可能已经写出的`FAILED`
  分别进入投递结果待人工恢复或清运恢复状态，不会被当作正常完成。
- 本批未改UART线协议、MCU固件或OneNet物模型版本；故障事件沿用现有`UART_PROTOCOL`枚举并以明细区分通信超时。

## 尚未完成

- 清运中断后的人工袋确认/必要时重新换袋流程仍未接入精简Runtime。
- 连续断网600秒释放用户和设备使用占用、热点页面必要数据、固件/镜像与成对现场测试仍未完成。
- 人工确认通信恢复并解除持续阻断的本机运维入口已完成本地候选；仍需随正式业务包发布并在现场验证，不能把本地通过等同于已部署。
