---
task_id: H-04
title: 真实 Native 充值
status: blocked
executor: human
owner: "unassigned — project owner / WeChat merchant operator"
effort_range: "0.5-1 person-days"
earliest_start: "after V-09 is done, EXT-WECHAT-NATIVE-READY is confirmed, and real-channel acceptance is explicitly authorized"
blocked_by:
  - V-09
  - EXT-WECHAT-NATIVE-READY
implementation_authorized: false
---

# H-04｜真实 Native 充值

> `EXT-WECHAT-NATIVE-READY` 是索引中登记的外部条件，不是 EcoBin 软件任务。该条件和 V-09 任一未满足时，本任务必须保持 `blocked`。

## 目标

在显式 opt-in 的 acceptance 环境中，用受控小额完成一笔真实微信 Native 扫码支付，并证明可信回调、主动查单、手续费、净额和机构资金流水一致。

## 要执行什么

- 由有权限的微信商户配置操作者准备真实普通商户 Native 支付条件和脱密验收清单。
- 在白名单试点机构创建一笔受控小额充值，生成真实二维码并由机构操作者主动扫码支付。
- 验证真实通知通过验签/解密后先进入 inbox，再由业务任务归并。
- 对原 `out_trade_no` 主动查单，并核对通知、查单和本地支付事实。
- 核对毛额、0.6% 向上取整手续费、净额、`RECHARGE_POSTED` 和机构账户投影。
- 保存标记为 `REAL` 的脱敏证据。

## 验收与证据

- [ ] acceptance 环境显式启用真实适配器、白名单试点机构和小额上限。
- [ ] 真实 Native 二维码由 V-09 的原充值单生成。
- [ ] 机构操作者实际扫码并完成微信支付。
- [ ] 可信回调进入 inbox，主动查单得到相同支付事实。
- [ ] 毛额、手续费、净额、机构不可变明细和账户投影一致。
- [ ] 重复通知和主动查单不会导致重复净额入账。
- [ ] 证据明确标记 `REAL`，商户、用户和支付凭证均脱敏。
- [ ] Fake 二维码、Fake 回调或本地状态注入均未用于完成本任务。

## 阻塞与最早开始

- 被 [V-09](v-09-native-recharge-software-loop.md) 阻塞：必须先有经过软件验证的充值状态机、可靠任务和客户端。
- 被 `EXT-WECHAT-NATIVE-READY` 阻塞：真实 Native 商户能力、配置和验收条件当前尚未就绪。
- 两项均满足并获得真实资金验收授权后才能开始。
- 完成后解除 H-05 和 H-06 的真实充值依赖。

## 排除范围

- 使用 Fake 或生产文案代替真实支付。
- 充值退款、其他支付产品、正式生产全面启用或外部企业商用。
- 在任务文件、日志或证据中保存 APIv3 key、私钥、完整回调或真实用户敏感数据。

## 权威来源

- [第 06 章：真实微信验收边界](../../detailed-design/06-funds-wechat.md)
- [第 07 章：M0 场景 2 和证据包](../../detailed-design/07-clients-operations-acceptance.md)
- [第 08 章：H-04](../../detailed-design/08-implementation-sequence.md)
- [I-031～I-035：充值与微信接口](../../interface-design/07-funds-recharge-withdrawal-wechat-i031-i035.md)
- [P0 范围基线](../../p0-scope-baseline.md)

## 进展记录

- 2026-07-23：正式任务发布；V-09 和真实微信条件均未完成，保持 `blocked`；尚未授权真实资金验收。
