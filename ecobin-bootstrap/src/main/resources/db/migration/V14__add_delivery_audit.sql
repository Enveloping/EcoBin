-- 投递订单增加人工审核阶段：建单进入「待审核」，租户管理员在网页后台审核通过后才返现入账。
ALTER TABLE biz_delivery_order
    ADD COLUMN audit_status TINYINT DEFAULT 0
        COMMENT '审核状态：0-待审核 1-审核通过 2-审核拒绝' AFTER status,
    ADD COLUMN audit_time   DATETIME DEFAULT NULL COMMENT '审核时间' AFTER audit_status,
    ADD COLUMN audit_remark VARCHAR(255) DEFAULT NULL COMMENT '审核备注' AFTER audit_time;

-- 存量历史订单视为已通过，避免历史数据卡在待审核
UPDATE biz_delivery_order SET audit_status = 1 WHERE audit_status = 0;
