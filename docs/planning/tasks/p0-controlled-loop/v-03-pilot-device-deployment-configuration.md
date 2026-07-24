---
task_id: V-03
title: 试点设备从库存到配置可用
status: blocked
executor: mixed
owner: "待指派 - 设备配置端到端切片负责人"
effort_range: "7-12 person-days"
earliest_start:
  software: "V-01、F-07、F-08、F-11 全部 done；可使用 MCU Stub 推进"
  integration: "software 完成，H-03 提供兼容 UART 1.0 固件候选，且 OneNet、COS、香橙派和真机可用"
  acceptance: "H-03 done，integration 完成并可取得配置双证明和真实安全状态证据"
phase_progress:
  software: not-started
  integration: not-started
  acceptance: not-started
blocked_by:
  - V-01
  - F-07
  - F-08
  - F-11
  - H-03
implementation_authorized: false
---

# V-03｜试点设备从库存到配置可用

## 目标

把一台真实库存资产安全建立为试点机构部署，发布完整配置并取得香橙派和 MCU 的精确应用证明，使后台能够可信判断设备是否具备后续投递或清运资格。

## 要构建什么

从平台库存资产开始，完成指定机构调试部署、全部投口和 UNKNOWN 运行投影的原子建立；实现部署生命周期、经营开关和实时投递/清运资格；实现整机及全部投口的不可变完整配置版本、可靠 OneNet 下发、香橙派 SQLite 保存、MCU staging/commit 和 `EDGE_SAVED → APPLIED` 双证明；在 Web 展示设备、配置版本、阻断原因及精确应用状态。

device 模块继续独占资产、部署、投口、配置和运行事实。OneNet、香橙派、MCU 与后端各层的接受或应用结果必须分别建模，任何下层 ACK 都不能冒充更强事实。

## 验收标准

- [ ] 资产、当前部署、全部 `1..N` 投口、整机及投口 UNKNOWN 投影和初始安全锁在一个事务内建立，失败不留部分部署。
- [ ] 部署码不可猜测，二维码不能以租户或机构明文作为可信作用域。
- [ ] 配置请求覆盖整机和所有投口，生成不可变递增版本、完整摘要和 MCU 子集摘要。
- [ ] 配置包含默认 30 秒本地继续选择、默认 500 克负重量检测阈值和清运电磁阀保护参数，并被双证明无损下发。
- [ ] OneNet 接受、MQTT/UART ACK、设备在线都不能冒充 `EDGE_SAVED` 或 `APPLIED`。
- [ ] 只有香橙派完整落盘形成 `EDGE_SAVED`，MCU 对同版本和摘要原子应用后形成 `APPLIED`。
- [ ] 只有最高期望配置精确 `APPLIED` 且投递门、协议、传感器、存储等安全条件成立时，才允许激活及开启经营；清运门仅有电磁阀通断推定状态和人工确认能力。
- [ ] 投递与清运资格分别计算；满溢或袋/基准问题不得错误阻断一次可恢复清运。
- [ ] 重同步复用原 application、command 和 task 身份，不创建第二份意图绕过失败。
- [ ] Web 能看到配置进展、具体 blocker 和设备/MCU 故障，不能由管理开关覆盖安全事实。
- [ ] 真机证据证明完整配置保存、MCU 应用、重启状态查询和故障阻断；Stub 不得关闭 integration 或 acceptance。

## 阻塞与最早开始

| 阶段 | 最早开始条件 |
|---|---|
| software | V-01、F-07、F-08、F-11 全部完成；可使用 MCU Stub 推进 |
| integration | software 完成，H-03 提供兼容 UART 1.0 固件候选，且 OneNet、COS、香橙派和真机可用 |
| acceptance | H-03 完成，integration 通过并能取得配置双证明和真实安全状态证据 |

H-03 是任务整体完成门，但不阻止满足基础依赖后的 software 阶段。发布本任务不代表已经授权修改代码、OneNet 配置或设备。

## 排除范围

- 实际用户投递、清运和满溢业务闭环；
- 设备调拨；
- MCU 固件实现本身；
- 旧 D1 或 AA/BB/CC/DD 协议兼容；
- 以持续传感器心跳替代冻结业务检测；
- 生产自动 HIL 或跨端契约 CI 门禁。

## 权威来源

- [详细设计第 02 章](../../detailed-design/02-identity-device-configuration.md)
- [可靠边缘第 03 章](../../detailed-design/03-reliable-edge-contracts.md)
- [实施依赖第 08 章](../../detailed-design/08-implementation-sequence.md)
- [I-016～I-020](../../interface-design/04-device-lifecycle-configuration-i016-i020.md)
- [I-041～I-045](../../interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)
- [I-046～I-050](../../interface-design/10-uart-protocol-i046-i050.md)
- [D-011～D-015](../../database-design/03-identity-device-d011-d015.md)
- [D-036～D-040](../../database-design/08-transactions-concurrency-d036-d040.md)

## 进展记录

- 2026-07-23：发布任务文件；仅完成设计与任务拆分，尚未授权实施。
- 2026-07-24：同步本地继续、负重量阈值和清运电磁阀配置验收；依赖及授权状态不变。
