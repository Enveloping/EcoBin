---
task_id: F-11
title: 香橙派 SQLite、OneNet、COS 与 UART 基础
status: in-progress
executor: agent
owner: "TBD / edge-iot-owner"
effort_range: "8-13 person-days"
earliest_start: "F-10 完成且获得显式授权后"
blocked_by:
  - F-10
implementation_authorized: true
---

# F-11｜香橙派 SQLite、OneNet、COS 与 UART 基础

> `status: in-progress`：F-10 已完成，且项目负责人已授权实施。香橙派对 OneNet 和
> 后端继续使用冻结的规范契约；现有单片机因修改成本采用双方已确定的固定帧协议，
> 由香橙派显式适配，不要求单片机原生实现 UART 1.0。

## 当前实施状态

- SQLite schema v4 已覆盖命令受理、统一工作槽、配置、可靠事件、照片、业务确认和
  固定帧接收代际；MQTT 回调只做校验、落库和服务受理响应。
- 固定帧适配器已经合入 `database-refactor`（截至 `b20dea1`）：`AA/BB/EE` 下行与
  `DD/EF` 完成上报只在香橙派硬件边界转换，不改变 OneNet、后端或业务事件结构。
- 云端九类命令和配置结构继续保留。固定帧 MCU 不具备的能力按已确认策略处理：配置
  只保存到香橙派；远程控制返回 `MCU_FEATURE_NOT_SUPPORTED`；状态返回
  `UNKNOWN/NOT_SAMPLED`；不得把本地受理伪造成 MCU 已执行。
- 照片已经接入设备直传 COS 队列与临时授权上传；开发环境真实 STS/COS
  upload/head/delete smoke 已人工通过，自动测试保证临时凭证只在执行期交给 SDK。
  仓库内仍缺少可重复执行真实 upload/head/delete 的诊断脚本。
- MQTT 重连复用同一个 Paho 网络循环；首次等待超时不再停止循环，掉线后由 Paho
  退避重连，避免旧实现约 21 秒、两次人为超时的恢复路径。当前只会继续转发
  `PENDING` 事件；进程在发布后、业务确认前退出时，`SENDING` 事件尚不能恢复转发。
  验收边界为“TCP 已感知掉线且网络/代理可用时 10 秒内恢复”；持续不可用时使用
  1～30 秒退避。仓库真实 OneNet 诊断脚本最近一次为 `1.110s <= 10s`。
- 已增加真实子进程强杀后的 SQLite 完整性测试、MQTT 网络循环生命周期测试和 COS
  上传参数 smoke。Python 3.11 硬件套件为 `165 passed, 5 subtests passed`；契约套件
  为 `43 passed, 64 subtests passed`。
- 真实固定帧 MCU 的线路、屏幕和执行器行为仍由 H-03 验收；它不是 F-11 软件完成门，
  F-11 软件通过也不得被描述为真机能力完整。

## 目标

在 Python 3.11 上建立香橙派可靠运行基础，以 SQLite 保存云边可靠事实，贯通命令持久
受理、整机作业槽、显式 MCU 协议适配、OneNet QoS 1、COS 照片队列和业务确认。当前
固定帧模式不承诺 MCU 作业重启恢复、物理命令重放或 Registry 状态快照。

## 要构建什么

- 建立 `EdgeStore` 和 SQLite schema/version/integrity 管理。
- 实现 command inbox、统一 work slot、投递/清运上下文、MCU 命令与事件、event/photo
  outbox、confirmation inbox、tombstone 和 fault 表族。
- 实现五个不可拆本地事务：收命令、收 MCU 事件、建边缘事件、收业务确认和登记照片。
- 实现持久 MQTT 会话、QoS 1、SQLite 驱动重发和稳定规范 JSON。
- 以显式协议模式实现 MCU 适配：当前部署使用 `fixed-frame` 和
  [`ecobin-mcu-fixed-frame-v1`](../../../../contracts/mcu-fixed-frame-v1.md)
  并对缺失能力明确拒绝或上报未知，不自动探测或失败回退。`uart-v1` 只保留为未来
  明确选择的可选实现，不是当前 F-11 完成门。
- 实现持久照片文件、原子登记、COS 临时授权上传、补授权、永久缺失和确认后清理。
- 使用 MCU Stub、进程强杀和网络故障注入验证基础可靠性。
- 固定帧重启后只处理香橙派本地事实：不重发未决物理命令，未决工作失败并释放槽位；
  不对无状态查询能力的 MCU 增加双事实冲突锁或伪造恢复快照。

## 验收条件

- [x] SQLite 使用受控持久目录、WAL、外键、busy timeout、`synchronous=FULL` 和短写事务。
- [x] 强杀进程后命令、作业槽、序号、事件、照片和确认不丢失。
- [x] MQTT 回调只完成校验和持久受理，不同步等待门、重量、相机或 COS。
- [x] MQTT PUBACK 不删除事件；只有稳定业务确认使事件和作业上下文进入清理资格。
- [x] COS 临时凭证不进入 SQLite、文件、摘要或日志。
- [x] OneNet/边缘规范事实严格按 F-10 机器契约处理；固定帧差异只存在于显式 MCU
  适配器，不泄漏为第二套云端业务协议。
- [x] `fixed-frame` 因无协议级状态查询而不重放旧物理命令，并把未决工作转为明确失败
  后释放本地槽位，不伪造 MCU 快照。
- [x] 一次投递 session 的中间本地轮次不进入 event/photo outbox，结束或选择超时只产生一个完成事件和四个整场照片槽。
- [x] 清运门分别保存电磁阀通断和人工关门确认，物理门位保持 `UNKNOWN`；断电或重启
  不自动宣称清运门已关闭。
- [x] MCU 不支持的配置、控制和状态能力分别采用“本地保存”“明确失败”“未知占位”，
  不阻断已支持的投递、清运完成主链，也不虚报物理执行成功。
- [x] MCU Stub 和固定帧软件测试只关闭软件部分，不冒充 H-03 真实设备验收。
- [x] 旧 D1、QoS 0、内存作业和 `/tmp` 照片不在正式路径；固定帧模式只能显式配置，
  不与 `uart-v1` 自动双解析或失败回退。

## 2026-07-28 复审决定

- [ ] 未获业务确认的 `SENDING` 事件重启重发：项目负责人决定本轮不处理，并接受当前
  强杀测试只证明持久行未丢失、不证明该状态可恢复重发的边界。
- [ ] MQTT 持久会话：暂不处理；继续使用当前 `clean_session=true` 行为。
- [ ] OneNet MQTT TLS：暂不处理；生产部署前仍需单独完成安全加固。
- [x] 首次物理动作前的照片时序：投递在发送开门命令前同步完成
  `FIRST_OPEN_*` 拍摄事实持久化；清运在解锁前保存 `FIRST_OPEN_*`，完成时另行保存
  `FINAL_CLOSE_*`。相机拍摄失败按既有契约登记为永久缺失，不阻断投递或清运；若连
  照片成功/缺失事实都无法持久化，则不发送首个物理动作。
- [ ] 真实 COS upload/head/delete 诊断脚本：暂不处理，等待后端相关建设；现有自动
  测试只验证 Mock SDK 调用参数。
- [x] OneNet 候选文件平台兼容：保留当前 13 个事件和全部业务字段，导入候选改用一层
  缩进和强制 LF，大小由 Windows 工作区的 `264820` bytes 降为 `189175` bytes，
  距离 `<262144` bytes 上限剩余 `72969` bytes。9 种过长的规范枚举值通过
  `enumDisplay` 映射为 1～20 字符的短显示说明，线上解码后的规范枚举值不变；本地
  校验和回归测试现在同时阻止超限、CRLF 和非法枚举说明。
- [ ] 固定帧同类型迟到 `DD/EF` 的错绑风险：项目负责人决定本轮不处理并接受残余风险。
- [ ] 固定帧兼容事件的合成身份语义：项目负责人决定本轮不处理并接受当前例外。

## 阻塞与最早开始

- [F-10](f-10-onenet-schema-uart-registry.md) 已完成，项目负责人已明确授权 F-11
  实施，因此当前状态为 `in-progress`。
- 当前完成门是本任务自身的 SQLite/COS/命令状态机/故障注入和固定帧适配软件验收。
  H-03 是下游真实设备门，不反向阻塞 F-11 软件任务完成；F-11 `done` 也不得冒充
  H-03 真机联调通过。

## 排除范围

- MCU 固件实现。
- V-03 的真实设备配置激活。
- V-04/V-06 的完整投递与恢复业务。
- V-07/V-08 的完整清运、满溢和基准业务。
- 后端订单、审核、钱包和资金业务。
- 用 MCU Stub 代替真实 HIL。
- 把固定帧协议缺少的 ACK、CRC、流程身份、状态查询或故障枚举伪造成 MCU 已提供。
- 当前固定帧模式下的 UART v1 快照完整性、SQLite/MCU 作业冲突对账和部署身份启动锁；
  项目负责人于 2026-07-28 明确它们不作为本轮完成条件。

## 权威来源

- [详细设计 03：可靠任务与边缘契约](../../detailed-design/03-reliable-edge-contracts.md)
- [I-041～I-045：OneNet、COS 与边缘确认](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)
- [I-046～I-050：UART 1.0](../../interface-design/10-uart-protocol-i046-i050.md)
- [详细设计 08：实施任务依赖图](../../detailed-design/08-implementation-sequence.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-24：同步本地继续投递和清运电子锁恢复事实；依赖与授权状态不变。
- 2026-07-24：F-10 软件机器来源完成且 MCU 负责人接受 Registry；F-11 取得可实施的
  唯一数值基线。
- 2026-07-24：项目负责人授权 F-11 实施；任务进入 `in-progress`。初步代码骨架已经
  形成，但主审确认 MQTT、UART 事件持久 ACK、恢复、COS 和测试证据仍有阻断问题，
  尚未达到 `in-review`。
- 2026-07-25：完成 OneNet 命令可靠受理和 `APPLY_CONFIGURATION` 配置纵切，补齐
  SQLite v2 恢复点、UART 分段停等、MCU 事件先持久化后 ACK、结果强绑定与超时恢复。
  本地和香橙派测试全部通过，远端服务以 `ECOBIN_TEST_MODE=true` 恢复为 `READY`；
  真实 MCU `HELLO` 无响应，因此真机配置激活和其余命令族仍待继续。
- 2026-07-27：项目负责人确认现有单片机不再原生实现 UART 1.0，F-10 转为 `done`；
  F-11 改为在香橙派侧显式选择固定帧适配器。适配 worktree 已完成协议解析、投递/清运
  完成事件映射和 67 项 Python 3.11 定向回归，仍待整理提交、合入及真实设备验收。
- 2026-07-28：固定帧适配、照片/COS 链路已合入 `database-refactor`。项目负责人确认
  当前妥协以保持香橙派—后端契约为优先：MCU 不支持的配置仅本地保存，远程控制返回
  不支持，状态使用未知占位；不增加物理命令重发、MCU 重启恢复、SQLite/MCU 双事实锁
  或部署身份启动锁。`uart-v1` 仅保留为可选历史路线，不作为当前完成门。
- 2026-07-28：补齐强杀恢复、MQTT 重连生命周期和 COS SDK 参数自动测试；修复 MQTT
  超时时停止 Paho 网络循环导致的迟缓重连。Python 3.11 硬件套件为
  `165 passed, 5 subtests passed`，契约套件为 `43 passed, 64 subtests passed`。
  `hardware/tools/mqtt_reconnect_smoke.py` 可重复执行真实掉线诊断，最近一次恢复
  `1.110s`，满足 10 秒边界。
  旧 `hardware_mcu/` 已按负责人授权删除，契约生成器默认不再要求该目录；只有显式
  `--include-hardware-mcu` 才生成可选 `uart-v1` MCU C 制品。
- 2026-07-28：再次按运行调用链和 OneNet 控制台前端校验快照复审。项目负责人决定
  本轮只修复照片时序：投递和清运首开照片事实先于首个物理命令持久化，清运终态照片
  在完成时另行拍摄。`SENDING` 重启重发、持久 MQTT 会话、TLS、真实 COS 诊断、
  同类型迟到帧和合成身份语义均按上述决定不在本轮处理。OneNet 候选已在不删除协议
  字段的前提下改为一层缩进、强制 LF 和短枚举显示说明，并新增 256 KiB/换行/说明
  格式自动门禁；F-11 暂保持 `in-progress`。
