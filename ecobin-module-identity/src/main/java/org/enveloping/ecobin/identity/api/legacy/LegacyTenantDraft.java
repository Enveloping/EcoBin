package org.enveloping.ecobin.identity.api.legacy;

/**
 * 仅供旧租户行为基线与集成测试使用的迁移输入。
 */
public record LegacyTenantDraft(
        String name,
        String code,
        String username,
        String password,
        String miniappAppid,
        String miniappSecret,
        String merchantNo,
        String contactName,
        String contactPhone,
        String address,
        Integer status) {
}
