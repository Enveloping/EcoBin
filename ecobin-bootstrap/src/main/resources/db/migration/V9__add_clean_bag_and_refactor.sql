-- 清运流程改造：垃圾袋追踪 + 去皮链式重量
-- 1. 垃圾袋追踪表：每个设备投口维护"当前那只袋"的去皮重量与袋号，换袋时更新（upsert）
CREATE TABLE IF NOT EXISTS biz_clean_bag (
    id          BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id   BIGINT        NOT NULL DEFAULT 1 COMMENT '租户ID',
    device_id   BIGINT        NOT NULL COMMENT '设备ID',
    door_index  INT           NOT NULL COMMENT '投口号（物理编号，第几个投口）',
    bag_qr      VARCHAR(64)            DEFAULT NULL COMMENT '当前垃圾袋二维码/编号',
    tare_weight DECIMAL(10,3)          DEFAULT NULL COMMENT '当前垃圾袋去皮重量（kg）',
    user_id     BIGINT                 DEFAULT NULL COMMENT '最近换袋清运人ID',
    create_time DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_clean_bag_device_door (device_id, door_index),
    INDEX idx_clean_bag_tenant_id (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='垃圾袋追踪（每投口当前袋去皮）';

-- 2. 清运订单改造：补充去皮链式重量字段（旧 audit_status 列保留但业务上废弃）
ALTER TABLE biz_clean_order ADD COLUMN bag_qr       VARCHAR(64)   DEFAULT NULL COMMENT '本次清运清走的垃圾袋编号';
ALTER TABLE biz_clean_order ADD COLUMN gross_weight DECIMAL(10,3) DEFAULT NULL COMMENT '清运毛重（设备上报）';
ALTER TABLE biz_clean_order ADD COLUMN tare_weight  DECIMAL(10,3) DEFAULT NULL COMMENT '去皮重量（清运时该投口当前去皮）';
ALTER TABLE biz_clean_order ADD COLUMN net_weight   DECIMAL(10,3) DEFAULT NULL COMMENT '实际清运量 = 毛重 - 去皮';
