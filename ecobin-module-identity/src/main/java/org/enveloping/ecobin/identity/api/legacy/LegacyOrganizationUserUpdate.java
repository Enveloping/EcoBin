package org.enveloping.ecobin.identity.api.legacy;

/**
 * 旧用户更新命令。所有字段除身份外均按旧 MyBatis 部分更新语义处理。
 */
public record LegacyOrganizationUserUpdate(
        LegacyOrganizationUserId userId,
        String password,
        String realName,
        String phone,
        String email,
        String nickname,
        String avatar,
        Integer role,
        Integer status) {
}
