# 提现功能设计笔记

> 📦 历史快照（已归档）。核心流程已实现并落入 `database-design.md`（biz_withdraw_order 等）；真实微信转账等待办已汇总至 `docs/open-items.md` §3。本文记录设计决策与实现差异。

> 状态：**核心流程已实现（2026-06-06）**，真实微信「商家转账到零钱」留 TODO 占位。
> 已确认决策：提现标的 = **现金返现余额**；到账方式 = **租户人工审核**后打款。

## 1. 背景与资金链路

用户在回收箱投递可回收物，按 `price × weight` 产生现金返现，累计为用户余额，可申请提现到微信。
各租户使用**自己的微信商户号**付款，资金从租户自有商户出 → 天然租户隔离。

### 已落地（V8 迁移 `V8__add_wallet_withdraw.sql`）

| 事项 | 实现 |
|------|------|
| 余额存储 | `sys_user.balance`（可用）+ `sys_user.pending_balance`（待审核），**未建独立钱包表** |
| 单价来源 | `biz_door.price`（元/kg），投递完成时服务端计算 `price × weight` 入账 |
| 提现记录 | `biz_withdraw_order`（status: 0-待审核 / 1-已通过 / 2-已驳回） |
| 入账触发 | `DeliveryOrderServiceImpl.completeDelivery`，投递完成时原子加余额 |
| 提现申请 | `WalletService.applyWithdraw`：可用→待审核（条件 SQL 防透支） |
| 审核通过 | `WalletService.auditWithdraw(pass)`：扣减待审核（资金转出） |
| 审核驳回 | `WalletService.auditWithdraw(reject)`：待审核退回可用 |
| 流水追溯 | 不建独立流水表：入账明细查 `biz_delivery_order`、出账明细查 `biz_withdraw_order`、余额当前值看 `sys_user` |

### 仍待办（依赖真实商户号配置，本地无法联调）

- `sys_tenant` 补支付凭证字段（`mch_apiv3_key` / `mch_cert_serial_no` / `mch_private_key`，AES 加密 + `WRITE_ONLY`）
- `WalletService.auditWithdraw` 通过分支接入微信「商家转账到零钱」API，回填 `transfer_no`
- 转账异步结果回查与失败补偿

## 2. 设计决策记录（与原设想的差异）

### 2.1 余额不建独立钱包表

原设想建 `biz_user_wallet` 独立表。**实际**：直接在 `sys_user` 加 `balance` + `pending_balance` 两个 `DECIMAL(12,2)` 字段。
理由：单用户单钱包，无复杂账户层级，独立表反而多一次 JOIN。

### 2.2 不建余额流水表

原设想建 `biz_balance_record` 记录每笔余额变动。**实际**：投递订单即入账流水，提现单即出账流水，不再冗余第三表。
余额追溯路径：`biz_delivery_order` → `biz_withdraw_order` → `sys_user` 当前值。

### 2.3 提现状态简化

原设想 6 状态（待审核/已通过/打款中/已到账/驳回/失败）。**实际**：3 状态（0-待审核 / 1-已通过 / 2-已驳回）。
"打款中/已到账/失败"等微信转账中间态留待真实 API 接入时扩展。

### 2.4 余额原子操作用条件 SQL，不用乐观锁

`UserMapper` 四个 `@Update` 注解 SQL：
- `addBalance`：`SET balance = balance + #{amount}`
- `freezeForWithdraw`：`SET balance = balance - #{amount}, pending_balance = pending_balance + #{amount} WHERE balance >= #{amount}`（返回 0 = 余额不足）
- `settlePending`：扣减待审核
- `refundPending`：待审核退回可用

MySQL 行锁保证原子性，无需额外 version 字段。

## 3. 接口一览

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET /api/app/wallet` | USER/CLEANER/DEVICE_ADMIN | 我的余额（balance + pendingBalance） |
| `POST /api/app/wallet/withdraw` | 同上 | 发起提现（body: amount） |
| `GET /api/app/wallet/withdraw` | 同上 | 我的提现记录分页 |
| `GET /api/system/withdraw?status=` | SUPER_ADMIN/TENANT | 提现单列表（租户隔离） |
| `POST /api/system/withdraw/{id}/audit` | SUPER_ADMIN/TENANT | 审核（body: pass, remark） |

## 4. 安全要点

- 余额操作全部走 `UserMapper` 原子 SQL，按主键 `id` 定位（userId 来自登录态/提现单），TenantLineInnerInterceptor 追加租户条件
- 提现冻结用条件 `WHERE balance >= #{amount}` 防透支，返回 0 时抛 `BusinessException(400, "余额不足")`
- 投递完成对已完成的订单拒绝重复上报，天然保证每单仅入账一次
- `completeDelivery` 加 `@Transactional`，入账与订单状态更新同事务
