---
task_id: V-10
title: 手动提现软件闭环
status: blocked
executor: agent
owner: "unassigned — backend/funds vertical-slice owner"
effort_range: "8-13 person-days"
earliest_start: "after V-05, V-09 and F-08 are done, and explicit implementation authorization"
blocked_by:
  - V-05
  - V-09
  - F-08
implementation_authorized: false
---

# V-10｜手动提现软件闭环

> 本任务只交付可验证的软件与 Fake 渠道闭环。2026-08-06 起，真实一次性授权、授权后商家转账和微信零钱到账必须由 H-05 单独证明；历史逐笔确认仅作兼容测试。

## 目标

让机构用户先完成一次免确认收款授权，再手动申请提现；审核员作出不可改金额的决定，系统双侧冻结用户和机构资金，并通过 Fake 微信适配器验证授权后自动收款、固定原单、渠道未知态、三种终态、`NOT_ENOUGH` 和恢复行为。客户端只显示后端权威状态。

## 要构建什么

- 实现机构手动提现配置、单活动提现槽、提现状态机和用户/机构双侧不可变资金明细。
- 实现授权申请、授权查单、不可变授权观察和当前有效授权；只有可信 `TAKING_EFFECT` 才开放新提现。
- 按冻结锁序实现提现创建；任一前置不满足时不建单、不写失败记录、不改变余额。
- 实现审核通过/驳回和客服渠道前终止；新流程不提供用户取消，审核通过只进入
  `READY_TO_SUBMIT`。
- 在短事务中建立唯一微信转账行、固定 `outBillNo` 和不可变请求摘要，再由可靠任务在事务外调用 Fake 渠道。
- 统一归并提交响应、回调、查单、撤销和对账观察；只有
  `SUCCESS/FAIL/CANCELLED` 可以结算或释放双侧冻结。
- 实现一次性授权页、小程序授权状态刷新和授权后自动收款；`WAIT_USER_CONFIRM` 提现确认入口只服务历史 `USER_CONFIRM` 单。
- 实现 `NOT_ENOUGH` 全平台出款闸门、单一暂停事件、聚合告警和版本化人工恢复。
- 完成 Web 提现审核/处置/闸门入口、小程序钱包/提现入口，以及真实 MySQL 并发和崩溃恢复测试。

## 验收条件

- [ ] P0 全部新提现进入 `PENDING_REVIEW`，免审阈值固定为 0。
- [ ] 新提现创建和渠道提交都锁定复核同一 `ACTIVE` 授权，并固化 `outAuthorizationNo + authorizationId`；授权关闭后不再创建新单，尚无转账行的活动提现渠道前终止并双侧释放，不能改挂新授权。
- [ ] 金额满足机构配置的手动最低/最高值，并且不超过 200 元硬上限。
- [ ] 同一钱包最多一个活动提现；创建时用户和机构额度在同一事务双侧冻结。
- [ ] 余额、机构额度、平台闸门、绑定或其他前置失败时不创建提现单和失败记录。
- [ ] 审核通过只进入 `READY_TO_SUBMIT`；驳回双侧释放且金额不可修改。
- [ ] 用户不能取消已经创建的手动提现；待审核单只能由有权限人员驳回。
- [ ] 客服只可终止 `READY_TO_SUBMIT` 且尚无微信转账行的原单。
- [ ] 转账行建立后禁止本地终止，所有重试和查证复用原 `outBillNo`。
- [ ] 超时、HTTP 错误、未知微信态、`SYSTEM_ERROR` 和 `NOT_ENOUGH` 均不释放冻结。
- [ ] 只有首个可信 `SUCCESS/FAIL/CANCELLED` 终态双侧结算一次并删除活动槽。
- [ ] 多笔 `NOT_ENOUGH` 只产生一个暂停期和一条聚合告警；恢复后继续原任务和原单号。
- [ ] Web 和小程序不把授权页返回、审核通过或微信受理显示为授权生效或成功到账。
- [ ] Fake 覆盖重复、乱序、崩溃、未知态和闸门并发；production 缺真实适配器时明确不可用。

## 阻塞与最早开始

- 被 [V-05](v-05-delivery-review-wallet-delta.md) 阻塞：提现必须建立在审核/纠错后的权威钱包和负余额联动上。
- 被 [V-09](v-09-native-recharge-software-loop.md) 阻塞：机构账户及其充值净额明细必须先成立。
- 被 [F-08](f-08-inbox-reliable-task-tracer.md) 阻塞：微信观察、提交、查单和恢复使用统一可靠任务设施。
- 全部前置为 `done` 且获得明确实施授权后，才可开始整体任务。
- 完成后解除 V-11、F-12 和 H-05 的相应依赖。

## 排除范围

- 真实授权、真实授权后商家转账受理和微信零钱到账，由 H-05 验收。
- 自动提现、免审提现和自动恢复出款闸门。
- 人工修改提现金额、换 `outBillNo`、伪造微信终态或把 Fake 结果用于 M0。

## 权威来源

- [第 08 章：实施任务依赖与 V-10](../../detailed-design/08-implementation-sequence.md)
- [第 06 章：提现、渠道归并和 NOT_ENOUGH](../../detailed-design/06-funds-wechat.md)
- [第 07 章：Web/小程序状态和真实验收边界](../../detailed-design/07-clients-operations-acceptance.md)
- [I-031～I-035：充值、提现与微信接口](../../interface-design/07-funds-recharge-withdrawal-wechat-i031-i035.md)
- [D-021～D-025：资金与微信数据模型](../../database-design/05-funds-wechat-d021-d025.md)
- [D-036～D-040：事务、锁序和可靠任务](../../database-design/08-transactions-concurrency-d036-d040.md)
- [D-046：免确认收款授权数据模型](../../database-design/10-merchant-transfer-authorization-d046.md)
- [I-056：免确认收款授权接口](../../interface-design/12-merchant-transfer-authorization-i056.md)

## 进展记录

- 2026-07-23：正式任务发布；软件依赖未完成，保持 `blocked`；尚未授权实施。
