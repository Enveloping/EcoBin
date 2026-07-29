package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryOrganizationScopeRef;

import java.util.Objects;

/**
 * 已按 tenant、organization、miniapp 顺序锁定并复核的小程序作用域。
 */
public record LockedMiniappDeliveryScope(
        String tenantCode,
        String organizationCode,
        String miniappAppId,
        SessionUid loginSessionUid,
        StartDeliveryOrganizationScopeRef organizationScopeRef) {

    public LockedMiniappDeliveryScope {
        tenantCode = nonBlank(tenantCode, "tenantCode");
        organizationCode = nonBlank(
                organizationCode,
                "organizationCode");
        miniappAppId = nonBlank(miniappAppId, "miniappAppId");
        Objects.requireNonNull(loginSessionUid, "loginSessionUid");
        Objects.requireNonNull(
                organizationScopeRef,
                "organizationScopeRef");
    }

    private static String nonBlank(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " must not be blank");
        }
        return value;
    }
}
