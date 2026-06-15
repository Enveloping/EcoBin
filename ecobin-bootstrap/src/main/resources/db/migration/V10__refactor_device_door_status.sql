-- 设备 / 投口实时状态拆分重整
-- 1. biz_device_status 收敛为「设备级·配置/物理/健康」：去重量/告警字段，补信号/固件版本
ALTER TABLE biz_device_status DROP COLUMN total_weight;
ALTER TABLE biz_device_status DROP COLUMN spill_alarm;
ALTER TABLE biz_device_status DROP COLUMN smoke_alarm;
ALTER TABLE biz_device_status ADD COLUMN rssi       INT          DEFAULT NULL COMMENT '信号强度（dBm）';
ALTER TABLE biz_device_status ADD COLUMN fw_version VARCHAR(32)  DEFAULT NULL COMMENT '固件版本';

-- 2. 新建投口实时状态表：每投口一条快照，承载重量/满溢/烟雾等投口级实时数据
CREATE TABLE IF NOT EXISTS biz_door_status (
    id               BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id        BIGINT        NOT NULL DEFAULT 1 COMMENT '租户ID',
    device_id        BIGINT        NOT NULL COMMENT '设备ID',
    door_index       INT           NOT NULL COMMENT '投口号（物理编号）',
    weight           DECIMAL(10,3)          DEFAULT NULL COMMENT '当前即时重量（kg）',
    fullness         INT                    DEFAULT NULL COMMENT '满溢度（0-100）',
    spill_alarm      TINYINT       NOT NULL DEFAULT 0 COMMENT '满溢报警：0-否 1-是',
    smoke_alarm      TINYINT       NOT NULL DEFAULT 0 COMMENT '烟雾报警：0-否 1-是',
    last_report_time DATETIME               DEFAULT NULL COMMENT '最后上报时间',
    UNIQUE KEY uk_door_status_device_door (device_id, door_index),
    INDEX idx_door_status_tenant_id (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='投口实时状态（投口级快照）';
