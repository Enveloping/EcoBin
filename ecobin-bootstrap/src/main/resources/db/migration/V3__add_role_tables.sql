-- =============================================
-- V3: 角色与登录主体重构
-- 1) 新增 sys_admin（平台管理员，无 tenant_id）
-- 2) sys_tenant 增加登录字段 + 小程序配置 + appid 唯一约束；id=1 保留为"平台池"
-- 3) sys_user 角色重映射为 1/2/3，原超管迁入 sys_admin
-- 角色体系：9-超管 8-管理员 7-租户 3-设备管理员 2-清运员 1-用户
-- =============================================

-- 1. 平台管理员表（无 tenant_id / openid / phone / email）
CREATE TABLE IF NOT EXISTS sys_admin (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    username    VARCHAR(50)  NOT NULL COMMENT '登录用户名',
    password    VARCHAR(255) NOT NULL COMMENT 'BCrypt加密密码',
    real_name   VARCHAR(50)           DEFAULT NULL COMMENT '真实姓名',
    role        TINYINT      NOT NULL COMMENT '角色：9-超级管理员 8-管理员',
    status      TINYINT      NOT NULL DEFAULT 1 COMMENT '状态：0-禁用 1-启用',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_admin_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='平台管理员';

-- 2. sys_tenant 扩展登录 + 小程序字段
ALTER TABLE sys_tenant
    ADD COLUMN username       VARCHAR(50)  DEFAULT NULL COMMENT '租户登录用户名',
    ADD COLUMN password       VARCHAR(255) DEFAULT NULL COMMENT 'BCrypt加密密码',
    ADD COLUMN miniapp_appid  VARCHAR(32)  DEFAULT NULL COMMENT '小程序AppID（未配置存NULL）',
    ADD COLUMN miniapp_secret VARCHAR(256) DEFAULT NULL COMMENT '小程序Secret（AES加密）',
    ADD COLUMN merchant_no    VARCHAR(64)  DEFAULT NULL COMMENT '微信商户号',
    ADD UNIQUE KEY uk_tenant_miniapp_appid (miniapp_appid);

-- 3. 数据迁移：把 sys_user 中的超级管理员（旧 role=1）迁入 sys_admin（role=9）
INSERT INTO sys_admin (username, password, real_name, role, status)
SELECT username, password, real_name, 9, status
FROM sys_user
WHERE role = 1 AND username IS NOT NULL;

-- 删除已迁出的超级管理员
DELETE FROM sys_user WHERE role = 1;

-- 4. sys_user 剩余角色重映射（CASE 基于行原值求值，无覆盖冲突）
--    旧 2-设备管理员→3, 旧 3-运营人员→1(废弃归并), 旧 4-清运员→2, 旧 5-普通用户→1
UPDATE sys_user
SET role = CASE role
    WHEN 2 THEN 3
    WHEN 3 THEN 1
    WHEN 4 THEN 2
    WHEN 5 THEN 1
    ELSE role
END;

-- 5. 平台池：id=1 不再是真实租户，重命名标识；真实租户自增从 2 开始
UPDATE sys_tenant SET name = '平台池', code = 'PLATFORM_POOL' WHERE id = 1;
ALTER TABLE sys_tenant AUTO_INCREMENT = 2;

-- 6. 兜底：若迁移后无任何管理员，则写入默认超管 admin/admin123
INSERT INTO sys_admin (username, password, real_name, role, status)
SELECT 'admin', '$2a$10$4lI4Vt97..D/ZV01YR/H2OpUwJalfktThtvArsxZUuzxa60dH5sPO', '系统管理员', 9, 1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM sys_admin);
