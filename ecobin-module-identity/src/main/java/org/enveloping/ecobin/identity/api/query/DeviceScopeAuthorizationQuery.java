package org.enveloping.ecobin.identity.api.query;

import java.util.Objects;

/**
 * Explicit target-path authorization for the device HTTP interface.
 *
 * <p>A platform path carries tenant and organization codes explicitly. A
 * staff path derives the tenant from the authenticated actor and only carries
 * an organization code.</p>
 */
public record DeviceScopeAuthorizationQuery(
        boolean platformPath,
        String tenantCode,
        String organizationCode,
        String requiredCapability) {

    public DeviceScopeAuthorizationQuery {
        Objects.requireNonNull(requiredCapability, "requiredCapability");
        if (requiredCapability.isBlank()) {
            throw new IllegalArgumentException(
                    "requiredCapability must not be blank");
        }
        if (organizationCode != null && organizationCode.isBlank()) {
            throw new IllegalArgumentException(
                    "organizationCode must not be blank");
        }
        if (platformPath
                && organizationCode != null
                && (tenantCode == null || tenantCode.isBlank())) {
            throw new IllegalArgumentException(
                    "platform organization access requires tenantCode");
        }
        if (!platformPath && tenantCode != null) {
            throw new IllegalArgumentException(
                    "staff access must derive tenant from its session");
        }
    }
}
