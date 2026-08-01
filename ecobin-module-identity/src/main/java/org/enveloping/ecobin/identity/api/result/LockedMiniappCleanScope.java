package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.persistence.CleanOrganizationScopeRef;

import java.util.Objects;

/** 已锁定并复核的清运小程序机构作用域。 */
public record LockedMiniappCleanScope(
        String tenantCode,
        String organizationCode,
        String miniappAppId,
        SessionUid loginSessionUid,
        CleanOrganizationScopeRef organizationScopeRef) {

    public LockedMiniappCleanScope {
        tenantCode = nonBlank(tenantCode, "tenantCode");
        organizationCode = nonBlank(
                organizationCode, "organizationCode");
        miniappAppId = nonBlank(miniappAppId, "miniappAppId");
        Objects.requireNonNull(loginSessionUid, "loginSessionUid");
        Objects.requireNonNull(
                organizationScopeRef,
                "organizationScopeRef");
    }

    private static String nonBlank(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    name + " must not be blank");
        }
        return value;
    }
}
