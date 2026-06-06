# 提现功能设计笔记（后续方向，暂未实现）

> 状态：方向已确认，**暂未实现**。本文为后续开工的设计储备。
> 已确认决策：提现标的 = **现金返现余额**；到账方式 = **租户人工审核**后打款。

## 1. 背景与资金链路

用户在回收箱投递可回收物，按 `price × weight` 产生现金返现，累计为用户余额，可申请提现到微信。
各租户使用**自己的微信商户号**付款，资金从租户自有商户出 → 天然租户隔离。

现状核查：
- `sys_tenant.merchant_no`（微信商户号）字段**已存在**（V3 迁移 / `Tenant.java`）。
- `biz_delivery_order` 已有 `price` / `weight` / `score`，但**未汇总成用户可提现余额**；当前无钱包表、无提现表。
- 仅有商户号不足以发起微信「商家转账到零钱」，缺签名所需的支付凭证字段。

## 2. 设计要点（三块）

### 2.1 租户支付凭证（`sys_tenant` 新增字段）

微信支付 API v3 签名需要，私钥类字段比照现有 `miniapp_secret` 做 **AES 加密存储**、`@JsonProperty(WRITE_ONLY)`：

| 字段 | 说明 |
|------|------|
| `merchant_no` | 微信商户号（**已存在**） |
| `mch_apiv3_key` | API v3 密钥（AES 加密） |
| `mch_cert_serial_no` | 商户证书序列号 |
| `mch_private_key` | 商户私钥（AES 加密） |

收款方 openid 用本租户小程序（`miniapp_appid` 已存在）下的用户 openid。

### 2.2 余额与流水（新表，均带 `tenant_id`，纳入 `TenantLineInnerInterceptor`）

- **用户余额**：推荐独立表 `biz_user_wallet`（`user_id`、`tenant_id`、`balance` 可用余额、`frozen` 冻结额），
  或在 `sys_user` 加 `balance` 字段。
- **资金流水** `biz_balance_record`：记录投递入账（`amount = price × weight`）与提现出账；
  余额变更走**事务 + 乐观锁/行锁**防并发与重复提现。
- 投递订单完成时 → 入账用户余额 + 写流水（需给 `biz_delivery_order` 补 `amount` 字段或入账时计算）。

### 2.3 提现申请 + 微信转账（新表 + 外部接口）

- **提现申请** `biz_withdraw_order`：`user_id`、`tenant_id`、`amount`、`status`（待审核 / 已通过 /
  打款中 / 已到账 / 驳回 / 失败）、申请时间、审核人、审核时间、微信转账单号。
- **流程**：用户发起提现 → 校验并**冻结**余额 → 状态"待审核" → 租户后台审核 →
  - 通过：调微信「商家转账到零钱」（付款方=租户商户号，收款方=用户 openid）→ 扣减余额 / 记流水 / 回填转账单号；
  - 驳回：解冻余额。
- **风控合规**：微信转账有单笔/单日限额、用户授权与实名要求；需处理转账异步结果回查与失败补偿。

## 3. 多租户注意

所有新表（`biz_user_wallet`、`biz_balance_record`、`biz_withdraw_order`）必须带 `tenant_id`，
由 MyBatis-Plus `TenantLineInnerInterceptor` 自动隔离，无需手写过滤条件。
