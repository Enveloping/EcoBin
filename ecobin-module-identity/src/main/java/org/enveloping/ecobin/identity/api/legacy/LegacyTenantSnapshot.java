package org.enveloping.ecobin.identity.api.legacy;

/**
 * 旧租户的安全快照，不包含密码和 AppSecret。
 */
public record LegacyTenantSnapshot(
        long tenantId,
        String name,
        String code,
        String username,
        String miniappAppid,
        String merchantNo,
        String contactName,
        String contactPhone,
        String address,
        Integer status) {
}
