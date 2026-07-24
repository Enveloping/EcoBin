package org.enveloping.ecobin.identity.api.legacy;

import java.math.BigDecimal;

/**
 * 仅用于维持旧 V1～V14 行为的创建输入；F-03/V-02 后删除。
 */
public record LegacyOrganizationUserDraft(
        long tenantId,
        String username,
        String password,
        String realName,
        String phone,
        String email,
        String openid,
        String unionid,
        String nickname,
        String avatar,
        Integer role,
        Integer status,
        BigDecimal balance,
        BigDecimal pendingBalance) {
}
