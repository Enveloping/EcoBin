-- 投递改造：设备活跃会话表。
-- 用户小程序「开启设备」时按 device_id upsert（覆盖为最近用户）；
-- 设备投递上报「上传后建单」时按 device_id 查未过期会话确定用户归属，过期/无则建无主单。
CREATE TABLE biz_device_session (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id   BIGINT       NOT NULL DEFAULT 1,
    device_id   BIGINT       NOT NULL COMMENT '设备ID（唯一，一台设备一行当前活跃用户）',
    user_id     BIGINT                DEFAULT NULL COMMENT '当前活跃用户ID',
    login_type  TINYINT               DEFAULT NULL COMMENT '登录方式：1-手机 2-IC卡 3-人脸 4-二维码',
    expire_time DATETIME              DEFAULT NULL COMMENT '会话过期时间（超过即无活跃用户）',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_device_session_device_id (device_id),
    INDEX idx_device_session_tenant_id (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备活跃会话（当前活跃用户）';
