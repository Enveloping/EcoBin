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
            String deploymentCode,
            String tenantCode,
            String organizationCode) {

        public RegistrationAttributionQuery {
            requireText(deploymentCode, "deploymentCode");
            requireText(tenantCode, "tenantCode");
            requireText(organizationCode, "organizationCode");
        }
    }

    record ResolvedRegistrationAttribution(
            String deploymentCode,
            OrganizationUserRegistrationAttributionRef persistenceRef) {

        public ResolvedRegistrationAttribution {
            requireText(deploymentCode, "deploymentCode");
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
