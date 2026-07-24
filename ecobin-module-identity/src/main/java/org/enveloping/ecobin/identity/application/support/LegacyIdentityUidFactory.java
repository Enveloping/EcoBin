package org.enveloping.ecobin.identity.application.support;

import org.enveloping.ecobin.identity.api.id.OrganizationUid;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.PrincipalUid;
import org.enveloping.ecobin.identity.api.id.TenantUid;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

/**
 * 在目标 UID 列落地前，为旧 V1～V14 行生成稳定、不暴露主键的迁移期 UUID。
 */
public final class LegacyIdentityUidFactory {

    private LegacyIdentityUidFactory() {
    }

    public static TenantUid tenant(long tenantId) {
        return new TenantUid(derive("tenant", tenantId));
    }

    public static OrganizationUid organization(long tenantId) {
        return new OrganizationUid(derive("organization", tenantId));
    }

    public static OrganizationUserUid organizationUser(long tenantId, long userId) {
        return new OrganizationUserUid(derive("organization-user:" + tenantId, userId));
    }

    public static PrincipalUid principal(String kind, long id) {
        return new PrincipalUid(derive("principal:" + kind, id));
    }

    private static UUID derive(String namespace, long id) {
        return UUID.nameUUIDFromBytes(
                ("ecobin:f02:legacy:" + namespace + ":" + id).getBytes(StandardCharsets.UTF_8));
    }
}
