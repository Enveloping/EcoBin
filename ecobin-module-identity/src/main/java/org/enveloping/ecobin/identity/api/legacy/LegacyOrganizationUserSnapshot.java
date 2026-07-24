package org.enveloping.ecobin.identity.api.legacy;

import java.math.BigDecimal;

/**
 * 不暴露 OpenID、密码或内部 Entity 的旧用户安全快照。
 */
public record LegacyOrganizationUserSnapshot(
        LegacyOrganizationUserId userId,
        long tenantId,
        String username,
        String realName,
        String phone,
        String nickname,
        String avatar,
        Integer role,
        Integer status,
        BigDecimal balance,
        BigDecimal pendingBalance) {
}
