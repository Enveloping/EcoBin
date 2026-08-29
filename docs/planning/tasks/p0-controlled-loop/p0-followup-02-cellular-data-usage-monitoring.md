---
task_id: P0-FOLLOWUP-02
title: 物联网卡流量计量、套餐余量与断流预警
status: needs-triage
executor: mixed
owner: "unassigned"
effort_range: "2-5 person-days"
earliest_start: "项目负责人后续明确恢复并完成供应商接口与计费周期确认后"
blocked_by: []
implementation_authorized: false
phase_progress:
  software: not-started
  integration: blocked-by-card-supplier-api-and-plan-facts
  acceptance: blocked-by-real-sim-billing-cycle-and-controlled-data-outage
---

# P0-FOLLOWUP-02｜物联网卡流量计量、套餐余量与断流预警

> 2026-08-29 项目负责人要求先记录、暂不实施。本任务不夹带进当前反向 SSH 修复，后续
> 重新开始前必须先完成范围和外部接口审查，并再次取得明确实施授权。

## 背景与目标

香橙派可以读取 Air780E RNDIS 网卡的收发字节计数；Air780E 官方 AT 固件还提供
`AT^DATAINFO` 上行/下行字节统计和掉电保存能力。两者都只能形成设备侧用量估算，不能
代替运营商或物联网卡供应商的计费账单。套餐总量、计费周期、权威已用量和剩余量必须由
供应商平台/API 按 ICCID 查询。

本任务后续需要形成三层事实：

1. 香橙派按选定的 Air780E RNDIS 接口采样 `rx_bytes/tx_bytes`，处理整机重启、模组重插、
   接口重建、计数回退和溢出，并把增量耐久化到边缘 SQLite；
2. 在当前 Air780E 固件和 AT 端口上验证 `AT^DATAINFO`，只把它作为掉电保持的交叉校验，
   不把模组计数冒充运营商账单；
3. 后端按 SIM 计费周期汇总设备估算值，并在取得供应商接口后保存权威套餐余量，Web 明确
   区分“设备估算”和“运营商账单”，提供阈值与预计耗尽告警。

## 开始实施前必须确认

- 物联网卡供应商、管理平台、ICCID 查询方式、API 鉴权方式和请求频率限制；
- 每张卡的套餐总量、计费周期、结转/共享池/定向流量等计费规则；
- OneNet 上报字段、采样与持久化周期、后端汇总粒度和 70%/85%/95% 等告警阈值；
- 流量耗尽时允许继续的本地动作、需要停止的新业务，以及屏幕和 Web 的用户提示；
- 照片上传、MQTT、远程维护、系统更新等流量分类是否只做总量统计，还是需要按用途估算。

## 初步验收条件

- [ ] 网卡或模组计数清零、回退、重启和重插不会造成负增量或重复累计。
- [ ] 本地累计即使断网也可恢复；恢复联网后只上报未确认快照，不制造重复月份用量。
- [ ] Web 和告警明确标注估算值与供应商权威值的来源、采样时间和计费周期。
- [ ] 信号正常但数据链路失败只能标记为“蜂窝数据不可用/疑似套餐耗尽”；只有供应商权威
      余额为零才能确认套餐耗尽。
- [ ] 流量耗尽时，OneNet、COS 和远程维护失败不会丢失已耐久化业务事实；续费恢复后可靠
      队列可以继续收敛。
- [ ] ICCID、供应商令牌和其他卡平台凭证不进入 OneNet 公共属性、普通日志或版本库。

## 已有现场事实

- 2026-08-29 真机已证明 `/sys/class/net/<rndis>/statistics/rx_bytes` 与 `tx_bytes` 可读；
- 当前 OneNet 物模型、设备 SQLite 和 Web 尚无专门的物联网卡用量字段；
- 合宙 Air780E 官方 AT 指令目录包含 `AT^DATAINFO`，实际端口、固件行为和保存周期仍需在
  本任务的受控真机阶段确认。

