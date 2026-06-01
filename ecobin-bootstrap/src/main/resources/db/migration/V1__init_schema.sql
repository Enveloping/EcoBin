-- =============================================
-- EcoBin 初始建表脚本
-- 所有业务表预留 tenant_id 字段（单租户阶段默认=1）
-- =============================================

-- 1. 系统租户表
CREATE TABLE IF NOT EXISTS sys_tenant (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    name        VARCHAR(100) NOT NULL COMMENT '租户名称',
    code        VARCHAR(50)  NOT NULL COMMENT '租户编码',
    contact_name VARCHAR(50)          DEFAULT NULL COMMENT '联系人',
    contact_phone VARCHAR(20)         DEFAULT NULL COMMENT '联系电话',
    address     VARCHAR(255)          DEFAULT NULL COMMENT '地址',
    status      TINYINT      NOT NULL DEFAULT 1 COMMENT '状态：0-禁用 1-启用',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统租户';

-- 2. 系统用户表
CREATE TABLE IF NOT EXISTS sys_user (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id   BIGINT       NOT NULL DEFAULT 1 COMMENT '租户ID',
    username    VARCHAR(50)  NOT NULL COMMENT '用户名',
    password    VARCHAR(255) NOT NULL COMMENT '加密密码',
    real_name   VARCHAR(50)           DEFAULT NULL COMMENT '真实姓名',
    phone       VARCHAR(20)           DEFAULT NULL COMMENT '手机号',
    email       VARCHAR(100)          DEFAULT NULL COMMENT '邮箱',
    role        TINYINT      NOT NULL COMMENT '角色：1-超级管理员 2-设备管理员 3-运营人员 4-清运员 5-普通用户',
    status      TINYINT      NOT NULL DEFAULT 1 COMMENT '状态：0-禁用 1-启用',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_username (username),
    INDEX idx_sys_user_tenant_id (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统用户';

-- 3. 设备表
CREATE TABLE IF NOT EXISTS biz_device (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id   BIGINT       NOT NULL DEFAULT 1 COMMENT '租户ID',
    sn          VARCHAR(50)  NOT NULL COMMENT '设备序列号',
    name        VARCHAR(100) NOT NULL COMMENT '设备名称',
    type        TINYINT      NOT NULL DEFAULT 1 COMMENT '设备类型：1-智能垃圾箱 2-滚动系统',
    lat         DECIMAL(10,7)         DEFAULT NULL COMMENT '纬度',
    lng         DECIMAL(10,7)         DEFAULT NULL COMMENT '经度',
    address     VARCHAR(255)          DEFAULT NULL COMMENT '安装地址',
    status      TINYINT      NOT NULL DEFAULT 1 COMMENT '状态：0-离线 1-在线 2-维护中',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_sn (sn),
    INDEX idx_device_tenant_id (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备';

-- 4. 投口表
CREATE TABLE IF NOT EXISTS biz_door (
    id          BIGINT      NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id   BIGINT      NOT NULL DEFAULT 1 COMMENT '租户ID',
    device_id   BIGINT      NOT NULL COMMENT '所属设备ID',
    door_index  TINYINT     NOT NULL COMMENT '投口号（1-6）',
    name        VARCHAR(50)          DEFAULT NULL COMMENT '投口名称',
    waste_type1 TINYINT     NOT NULL COMMENT '一级分类：1-厨余 2-可回收 3-有害 4-其他',
    waste_type2 TINYINT     NOT NULL DEFAULT 0 COMMENT '二级分类：0-不区分 1-纸类 2-塑料 3-织物 4-金属 5-其他',
    enabled     TINYINT     NOT NULL DEFAULT 1 COMMENT '0-禁用 1-启用',
    sort_order  INT                  DEFAULT 0 COMMENT '排序',
    create_time DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_door_device_id (device_id),
    INDEX idx_door_tenant_id (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='投口';

-- 5. 投递订单表
CREATE TABLE IF NOT EXISTS biz_delivery_order (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id   BIGINT       NOT NULL DEFAULT 1 COMMENT '租户ID',
    order_sn    VARCHAR(50)  NOT NULL COMMENT '订单编号',
    device_id   BIGINT                DEFAULT NULL COMMENT '设备ID',
    door_id     BIGINT                DEFAULT NULL COMMENT '投口ID',
    user_id     BIGINT                DEFAULT NULL COMMENT '投递用户ID',
    waste_type1 TINYINT      NOT NULL COMMENT '一级分类',
    waste_type2 TINYINT      NOT NULL DEFAULT 0 COMMENT '二级分类',
    weight      DECIMAL(10,3)         DEFAULT NULL COMMENT '重量（kg）',
    price       DECIMAL(10,2)         DEFAULT NULL COMMENT '单价',
    score       INT                   DEFAULT 0 COMMENT '获得积分',
    login_type  TINYINT               DEFAULT NULL COMMENT '登录方式：1-手机 2-IC卡 3-人脸 4-二维码',
    status      TINYINT      NOT NULL DEFAULT 0 COMMENT '状态：0-正常 -1-异常',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '投递时间',
    UNIQUE KEY uk_delivery_order_sn (order_sn),
    INDEX idx_delivery_device_id (device_id),
    INDEX idx_delivery_user_id (user_id),
    INDEX idx_delivery_tenant_id (tenant_id),
    INDEX idx_delivery_create_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='投递订单';

-- 6. 清运订单表
CREATE TABLE IF NOT EXISTS biz_clean_order (
    id           BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id    BIGINT       NOT NULL DEFAULT 1 COMMENT '租户ID',
    order_sn     VARCHAR(50)  NOT NULL COMMENT '订单编号',
    device_id    BIGINT                DEFAULT NULL COMMENT '设备ID',
    door_id      BIGINT                DEFAULT NULL COMMENT '投口ID',
    user_id      BIGINT                DEFAULT NULL COMMENT '清运员ID',
    waste_type1  TINYINT      NOT NULL COMMENT '一级分类',
    waste_type2  TINYINT      NOT NULL DEFAULT 0 COMMENT '二级分类',
    weight       DECIMAL(10,3)         DEFAULT NULL COMMENT '清理重量（kg）',
    audit_status TINYINT      NOT NULL DEFAULT 0 COMMENT '审核状态：0-待审核 1-审核通过 2-审核拒绝',
    status       TINYINT      NOT NULL DEFAULT 0 COMMENT '订单状态：0-创建 1-完成 2-取消',
    create_time  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_clean_order_sn (order_sn),
    INDEX idx_clean_device_id (device_id),
    INDEX idx_clean_user_id (user_id),
    INDEX idx_clean_tenant_id (tenant_id),
    INDEX idx_clean_create_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='清运订单';

-- 7. 设备实时状态表
CREATE TABLE IF NOT EXISTS biz_device_status (
    id               BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id        BIGINT       NOT NULL DEFAULT 1 COMMENT '租户ID',
    device_id        BIGINT       NOT NULL COMMENT '设备ID（一对一）',
    online           TINYINT      NOT NULL DEFAULT 0 COMMENT '0-离线 1-在线',
    total_weight     DECIMAL(10,3)         DEFAULT NULL COMMENT '当前总重量',
    spill_alarm      TINYINT      NOT NULL DEFAULT 0 COMMENT '满溢报警：0-否 1-是',
    smoke_alarm      TINYINT      NOT NULL DEFAULT 0 COMMENT '烟雾报警：0-否 1-是',
    voltage          DECIMAL(5,2)          DEFAULT NULL COMMENT '电压',
    last_report_time DATETIME              DEFAULT NULL COMMENT '最后上报时间',
    UNIQUE KEY uk_device_status_device_id (device_id),
    INDEX idx_device_status_tenant_id (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备实时状态';

-- 8. 重量变更记录表
CREATE TABLE IF NOT EXISTS biz_weight_record (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    tenant_id   BIGINT       NOT NULL DEFAULT 1 COMMENT '租户ID',
    device_id   BIGINT       NOT NULL COMMENT '设备ID',
    door_id     BIGINT                DEFAULT NULL COMMENT '投口ID',
    weight      DECIMAL(10,3)         DEFAULT NULL COMMENT '重量',
    record_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
    INDEX idx_weight_device_id (device_id),
    INDEX idx_weight_tenant_id (tenant_id),
    INDEX idx_weight_record_time (record_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='重量变更记录';

-- =============================================
-- 初始化数据：默认租户 + 超级管理员
-- =============================================

INSERT INTO sys_tenant (id, name, code, contact_name, status) VALUES (1, '默认机构', 'DEFAULT', '系统管理员', 1) AS new
ON DUPLICATE KEY UPDATE name = new.name;

-- 密码: admin123 (BCrypt 加密)
INSERT INTO sys_user (id, tenant_id, username, password, real_name, role, status) VALUES
(1, 1, 'admin', '$2a$10$4lI4Vt97..D/ZV01YR/H2OpUwJalfktThtvArsxZUuzxa60dH5sPO', '系统管理员', 1, 1) AS new
ON DUPLICATE KEY UPDATE username = new.username;
