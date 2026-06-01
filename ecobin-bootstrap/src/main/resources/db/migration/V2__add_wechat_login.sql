-- =============================================
-- V2: 新增微信登录支持
-- sys_user 新增 openid/unionid/nickname/avatar
-- username/password 改为可空（微信用户无需用户名密码）
-- =============================================

ALTER TABLE sys_user
    MODIFY COLUMN username VARCHAR(50)  NULL COMMENT '用户名（微信用户可空）',
    MODIFY COLUMN password VARCHAR(255) NULL COMMENT '加密密码（微信用户可空）',
    ADD COLUMN openid   VARCHAR(64)  NULL AFTER email COMMENT '微信 openid',
    ADD COLUMN unionid  VARCHAR(64)  NULL AFTER openid COMMENT '微信 unionid',
    ADD COLUMN nickname VARCHAR(100) NULL AFTER unionid COMMENT '微信昵称',
    ADD COLUMN avatar   VARCHAR(500) NULL AFTER nickname COMMENT '微信头像 URL',
    ADD UNIQUE INDEX uk_openid (openid);
