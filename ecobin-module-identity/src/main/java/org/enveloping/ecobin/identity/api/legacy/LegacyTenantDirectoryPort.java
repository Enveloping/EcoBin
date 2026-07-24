package org.enveloping.ecobin.identity.api.legacy;

/**
 * F-02 迁移后用于旧行为基线的租户目录过渡端口。
 */
public interface LegacyTenantDirectoryPort {

    LegacyTenantSnapshot create(LegacyTenantDraft draft);
}
