package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.persistence.OrganizationUserRegistrationAttributionRef;

import java.util.Objects;
import java.util.Optional;

/**
 * Consumer-shaped participant port for resolving an optional trusted device
 * registration attribution. Device implements it while the frozen
 * {@code device -> identity} Maven dependency remains intact.
 */
public interface OrganizationUserRegistrationAttributionPort {

    Optional<ResolvedRegistrationAttribution> resolve(
            RegistrationAttributionQuery query);

    record RegistrationAttributionQuery(
            String deviceCode,
            String tenantCode,
            String organizationCode) {

        public RegistrationAttributionQuery {
            requireText(deviceCode, "deviceCode");
            requireText(tenantCode, "tenantCode");
            requireText(organizationCode, "organizationCode");
        }
    }

    record ResolvedRegistrationAttribution(
            String deviceCode,
            OrganizationUserRegistrationAttributionRef persistenceRef) {

        public ResolvedRegistrationAttribution {
            requireText(deviceCode, "deviceCode");
            Objects.requireNonNull(persistenceRef, "persistenceRef");
        }
    }

    private static void requireText(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    name + " must not be blank");
        }
    }
}
