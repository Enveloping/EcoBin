-- =============================================
-- EcoBin 初始建表脚本 (H2 兼容版，仅测试环境)
-- =============================================

-- 1. 系统租户表
CREATE TABLE IF NOT EXISTS sys_tenant (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    code        VARCHAR(50)  NOT NULL,
    contact_name VARCHAR(50)          DEFAULT NULL,
    contact_phone VARCHAR(20)         DEFAULT NULL,
    address     VARCHAR(255)          DEFAULT NULL,
    status      TINYINT      NOT NULL DEFAULT 1,
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_code (code)
);

-- 2. 系统用户表
CREATE TABLE IF NOT EXISTS sys_user (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id   BIGINT       NOT NULL DEFAULT 1,
    username    VARCHAR(50)  NOT NULL,
    password    VARCHAR(255) NOT NULL,
    real_name   VARCHAR(50)           DEFAULT NULL,
    phone       VARCHAR(20)           DEFAULT NULL,
    email       VARCHAR(100)          DEFAULT NULL,
    role        TINYINT      NOT NULL,
    status      TINYINT      NOT NULL DEFAULT 1,
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_username (username),
    INDEX idx_sys_user_tenant_id (tenant_id)
);

-- 3. 设备表
CREATE TABLE IF NOT EXISTS biz_device (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id   BIGINT       NOT NULL DEFAULT 1,
    sn          VARCHAR(50)  NOT NULL,
    name        VARCHAR(100) NOT NULL,
    type        TINYINT      NOT NULL DEFAULT 1,
    lat         DECIMAL(10,7)         DEFAULT NULL,
    lng         DECIMAL(10,7)         DEFAULT NULL,
    address     VARCHAR(255)          DEFAULT NULL,
    status      TINYINT      NOT NULL DEFAULT 1,
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_sn (sn),
    INDEX idx_device_tenant_id (tenant_id)
);

-- 4. 投口表
CREATE TABLE IF NOT EXISTS biz_door (
    id          BIGINT      NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id   BIGINT      NOT NULL DEFAULT 1,
    device_id   BIGINT      NOT NULL,
    door_index  TINYINT     NOT NULL,
    name        VARCHAR(50)          DEFAULT NULL,
    waste_type1 TINYINT     NOT NULL,
    waste_type2 TINYINT     NOT NULL DEFAULT 0,
    enabled     TINYINT     NOT NULL DEFAULT 1,
    sort_order  INT                  DEFAULT 0,
    create_time DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_door_device_id (device_id),
    INDEX idx_door_tenant_id (tenant_id)
);

-- 5. 投递订单表
CREATE TABLE IF NOT EXISTS biz_delivery_order (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id   BIGINT       NOT NULL DEFAULT 1,
    order_sn    VARCHAR(50)  NOT NULL,
    device_id   BIGINT                DEFAULT NULL,
    door_id     BIGINT                DEFAULT NULL,
    user_id     BIGINT                DEFAULT NULL,
    waste_type1 TINYINT      NOT NULL,
    waste_type2 TINYINT      NOT NULL DEFAULT 0,
    weight      DECIMAL(10,3)         DEFAULT NULL,
    price       DECIMAL(10,2)         DEFAULT NULL,
    score       INT                   DEFAULT 0,
    login_type  TINYINT               DEFAULT NULL,
    status      TINYINT      NOT NULL DEFAULT 0,
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_delivery_order_sn (order_sn),
    INDEX idx_delivery_device_id (device_id),
    INDEX idx_delivery_user_id (user_id),
    INDEX idx_delivery_tenant_id (tenant_id),
    INDEX idx_delivery_create_time (create_time)
);

-- 6. 清运订单表
CREATE TABLE IF NOT EXISTS biz_clean_order (
    id           BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id    BIGINT       NOT NULL DEFAULT 1,
    order_sn     VARCHAR(50)  NOT NULL,
    device_id    BIGINT                DEFAULT NULL,
    door_id      BIGINT                DEFAULT NULL,
    user_id      BIGINT                DEFAULT NULL,
    waste_type1  TINYINT      NOT NULL,
    waste_type2  TINYINT      NOT NULL DEFAULT 0,
    weight       DECIMAL(10,3)         DEFAULT NULL,
    audit_status TINYINT      NOT NULL DEFAULT 0,
    status       TINYINT      NOT NULL DEFAULT 0,
    create_time  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_clean_order_sn (order_sn),
    INDEX idx_clean_device_id (device_id),
    INDEX idx_clean_user_id (user_id),
    INDEX idx_clean_tenant_id (tenant_id),
    INDEX idx_clean_create_time (create_time)
);

-- 7. 设备实时状态表
CREATE TABLE IF NOT EXISTS biz_device_status (
    id               BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id        BIGINT       NOT NULL DEFAULT 1,
    device_id        BIGINT       NOT NULL,
    online           TINYINT      NOT NULL DEFAULT 0,
    total_weight     DECIMAL(10,3)         DEFAULT NULL,
    spill_alarm      TINYINT      NOT NULL DEFAULT 0,
    smoke_alarm      TINYINT      NOT NULL DEFAULT 0,
    voltage          DECIMAL(5,2)          DEFAULT NULL,
    last_report_time DATETIME              DEFAULT NULL,
    UNIQUE KEY uk_device_status_device_id (device_id),
    INDEX idx_device_status_tenant_id (tenant_id)
);

-- 8. 重量变更记录表
CREATE TABLE IF NOT EXISTS biz_weight_record (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id   BIGINT       NOT NULL DEFAULT 1,
    device_id   BIGINT       NOT NULL,
    door_id     BIGINT                DEFAULT NULL,
    weight      DECIMAL(10,3)         DEFAULT NULL,
    record_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_weight_device_id (device_id),
    INDEX idx_weight_tenant_id (tenant_id),
    INDEX idx_weight_record_time (record_time)
);

-- =============================================
-- 初始化数据 (H2 兼容语法)
-- =============================================

MERGE INTO sys_tenant (id, name, code, contact_name, status) KEY (id)
VALUES (1, '默认机构', 'DEFAULT', '系统管理员', 1);

-- 密码: admin123 (BCrypt 加密)
MERGE INTO sys_user (id, tenant_id, username, password, real_name, role, status) KEY (id)
VALUES (1, 1, 'admin', '$2a$10$4lI4Vt97..D/ZV01YR/H2OpUwJalfktThtvArsxZUuzxa60dH5sPO', '系统管理员', 1, 1);
