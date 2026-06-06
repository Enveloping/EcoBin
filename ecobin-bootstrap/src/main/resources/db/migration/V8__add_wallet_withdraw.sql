-- =============================================
-- V8: 钱包入账 + 提现
-- 余额直接挂在 sys_user（balance 可用 / pending_balance 待审核）；
-- 投口单价 biz_door.price（元/kg），投递完成时按 单价 × 重量 入账；
-- 提现申请单独建表 biz_withdraw_order，记录申请—审核—（占位）转账全流程。
-- 不单独建余额流水表：入账明细查 biz_delivery_order，出账明细查 biz_withdraw_order。
-- =============================================

ALTER TABLE sys_user
    ADD COLUMN balance         DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '可用余额' AFTER status,
    ADD COLUMN pending_balance DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '待审核余额（提现申请中冻结）' AFTER balance;

ALTER TABLE biz_door
    ADD COLUMN price DECIMAL(10,2) DEFAULT NULL COMMENT '单价（元/kg），投递完成按 单价×重量 返现' AFTER waste_type2;

CREATE TABLE IF NOT EXISTS biz_withdraw_order (
    id           BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id    BIGINT        NOT NULL DEFAULT 1 COMMENT '租户ID',
    user_id      BIGINT        NOT NULL COMMENT '申请用户ID',
    amount       DECIMAL(12,2) NOT NULL COMMENT '提现金额',
    status       TINYINT       NOT NULL DEFAULT 0 COMMENT '0-待审核 1-已通过 2-已驳回',
    audit_by     BIGINT                 DEFAULT NULL COMMENT '审核租户主体ID',
    audit_time   DATETIME               DEFAULT NULL COMMENT '审核时间',
    audit_remark VARCHAR(255)           DEFAULT NULL COMMENT '审核备注',
    transfer_no  VARCHAR(64)            DEFAULT NULL COMMENT '微信转账单号（真实转账接入后回填）',
    create_time  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '申请时间',
    update_time  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_withdraw_user_id (user_id),
    INDEX idx_withdraw_tenant_id (tenant_id),
    INDEX idx_withdraw_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='提现申请单';
