package org.enveloping.ecobin.identity.api.legacy;

/**
 * 旧用户目录的过渡公开面，防止其他模块导入 identity Entity/Mapper/内部 Service。
 */
public interface LegacyOrganizationUserDirectoryPort {

    LegacyOrganizationUserSnapshot create(LegacyOrganizationUserDraft draft);

    LegacyOrganizationUserSnapshot find(LegacyOrganizationUserId userId);

    LegacyOrganizationUserSnapshot update(LegacyOrganizationUserUpdate update);

    LegacyOrganizationUserSnapshot changeRole(LegacyOrganizationUserId userId, int role);
}
