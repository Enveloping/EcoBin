---
task_id: H-05
title: 真实商家转账与微信零钱到账
status: blocked
executor: human
owner: "unassigned — project owner / designated test user"
effort_range: "0.5-1 person-days"
earliest_start: "after V-10 and H-04 are done, EXT-WECHAT-TRANSFER-READY is confirmed, and real-channel acceptance is explicitly authorized"
blocked_by:
  - V-10
  - H-04
  - EXT-WECHAT-TRANSFER-READY
implementation_authorized: false
---

# H-05｜真实商家转账与微信零钱到账

> `EXT-WECHAT-TRANSFER-READY` 是外部条件，不是软件任务。Fake 渠道、页面返回或微信“已受理”都不能关闭本任务。

## 目标

使用指定测试用户和受控小额完成一笔真实商家转账，必要时真实调起用户确认页，并证明微信零钱实际到账、固定原 `outBillNo` 和双账本终态一致。

## 要执行什么

- 在 acceptance 环境准备真实商家转账能力、白名单机构/用户、小额上限和脱密证据路径。
- 使用 V-10 的正式提现入口创建一笔受控小额提现，完成审核和双侧冻结。
- 由真实渠道提交原单，记录微信受理及后续回调/查单观察。
- 如进入 `WAIT_USER_CONFIRM`，由指定测试用户真实调起确认页；返回后继续查询后端。
- 等待可信微信终态，并核对用户微信零钱实际到账、提现状态、活动槽和用户/机构双账本。
- 保存标记为 `REAL` 的脱敏证据和原单稳定业务引用。

## 验收与证据

- [ ] 真实提交使用 V-10 已固定的唯一 `outBillNo`，没有换号或本地重建。
- [ ] 微信真实受理只推进渠道处理中，不提前结算双账本。
- [ ] 需要用户确认时，指定测试用户真实调起确认页；页面返回不直接标记成功。
- [ ] 最终从可信通知或查单取得 `SUCCESS`。
- [ ] 指定测试用户微信零钱实际到账，且金额与原提现单一致。
- [ ] 提现、活动槽、用户钱包、机构账户和双方不可变明细均只结算一次。
- [ ] 证据明确标记 `REAL` 并脱敏；Fake、截图文案或人工改库未参与终态。

## 阻塞与最早开始

- 被 [V-10](v-10-manual-withdrawal-software-loop.md) 阻塞：必须先完成双冻结、固定原单和渠道归并软件闭环。
- 被 [H-04](h-04-real-native-recharge.md) 阻塞：M0 资金验收先证明真实机构充值和额度来源。
- 被 `EXT-WECHAT-TRANSFER-READY` 阻塞：真实商家转账能力和验收条件当前尚未就绪。
- 全部条件满足并获得真实资金验收授权后才能开始。
- 完成后解除 H-06 的真实零钱到账依赖。

## 排除范围

- 自动提现、免确认授权或非受控用户/金额。
- 修改提现金额、换 `outBillNo`、人工伪造终态或直接改账本。
- 使用 Fake 适配器、微信受理响应或确认页返回代替真实零钱到账。

## 权威来源

- [第 06 章：微信商家转账与终态归并](../../detailed-design/06-funds-wechat.md)
- [第 07 章：M0 场景 8 和证据包](../../detailed-design/07-clients-operations-acceptance.md)
- [第 08 章：H-05](../../detailed-design/08-implementation-sequence.md)
- [I-031～I-035：提现与微信接口](../../interface-design/07-funds-recharge-withdrawal-wechat-i031-i035.md)
- [P0 范围基线](../../p0-scope-baseline.md)

## 进展记录

- 2026-07-23：正式任务发布；软件、真实充值和商家转账外部条件未完成，保持 `blocked`；尚未授权真实资金验收。
