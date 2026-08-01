package org.enveloping.ecobin.identity.api.query;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;

import java.util.Objects;

public record DeliveryOrganizationUserFilterQuery(
        String tenantCode,
        String organizationCode,
        OrganizationUserUid organizationUserUid) {

    public DeliveryOrganizationUserFilterQuery {
        tenantCode = requiredCode(tenantCode, "tenantCode");
        organizationCode = requiredCode(
                organizationCode,
                "organizationCode");
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
    }

    private static String requiredCode(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    name + " must not be blank");
        }
        return value.trim();
    }
}
