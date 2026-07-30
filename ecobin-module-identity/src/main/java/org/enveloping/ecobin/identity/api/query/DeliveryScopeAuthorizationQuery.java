package org.enveloping.ecobin.identity.api.query;

/**
 * Explicit organization target for delivery Web authorization.
 *
 * <p>Platform mirror paths carry both public codes. Staff paths derive the
 * tenant from the authenticated Web session and therefore only carry the
 * organization code.</p>
 */
public record DeliveryScopeAuthorizationQuery(
        boolean platformPath,
        String tenantCode,
        String organizationCode) {

    public DeliveryScopeAuthorizationQuery {
        organizationCode = requiredCode(
                organizationCode,
                "organizationCode");
        if (platformPath) {
            tenantCode = requiredCode(tenantCode, "tenantCode");
        } else if (tenantCode != null) {
            throw new IllegalArgumentException(
                    "staff delivery access must derive tenant from session");
        }
    }

    private static String requiredCode(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    name + " must not be blank");
        }
        return value.trim();
    }
}
