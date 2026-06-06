-- =============================================
-- V7: 投递两阶段流程（开投口建记录 → 设备 IoT 上报回填）
-- delivery_token：用户开投口时生成下发，设备关投口上报时携带，用于关联回填同一条投递记录；
-- delivery_status：区分进行中（已开投口待回填）与已完成。历史/后台直接创建的订单默认已完成。
-- delivery_token 可空（后台手工创建无此标识），NULL 不参与唯一性判定。
-- =============================================
ALTER TABLE biz_delivery_order
    ADD COLUMN delivery_token  VARCHAR(64) DEFAULT NULL COMMENT '投递标识符（开投口生成，关投口上报回填）' AFTER order_sn,
    ADD COLUMN delivery_status TINYINT     NOT NULL DEFAULT 1 COMMENT '投递阶段：0-进行中 1-已完成' AFTER status,
    ADD UNIQUE INDEX uk_delivery_token (delivery_token);
