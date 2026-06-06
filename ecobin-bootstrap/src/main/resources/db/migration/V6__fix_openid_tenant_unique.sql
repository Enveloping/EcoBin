-- =============================================
-- V6: 修复 openid 唯一约束与多租户设计冲突
-- 原 uk_openid(openid) 为全局唯一，导致同一微信 openid 无法在第二个租户下注册；
-- 设计要求「同一 openid 在不同租户下为独立 sys_user 记录」，改为复合唯一。
-- openid 可空（非微信用户为 NULL），NULL 不参与唯一性判定，符合预期。
-- =============================================
ALTER TABLE sys_user
    DROP INDEX uk_openid,
    ADD UNIQUE INDEX uk_tenant_openid (tenant_id, openid);
