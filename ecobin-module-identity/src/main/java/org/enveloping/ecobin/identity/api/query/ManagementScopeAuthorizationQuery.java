package org.enveloping.ecobin.identity.api.query;

import java.util.Objects;

/**
 * Real-time authorization for audit, alert, statistics and other management
 * read models. A miniapp-staff request always derives its organization from
 * the authenticated session.
 */
public record ManagementScopeAuthorizationQuery(
        Channel channel,
        boolean platformPath,
        String tenantCode,
        String organizationCode,
        String requiredCapability) {

    public ManagementScopeAuthorizationQuery {
        Objects.requireNonNull(channel, "channel");
        requiredCapability = required(requiredCapability,
                "requiredCapability");
        tenantCode = optional(tenantCode);
        organizationCode = optional(organizationCode);
        if (channel == Channel.MINIAPP_STAFF
                && (platformPath || tenantCode != null
                || organizationCode != null)) {
            throw new IllegalArgumentException(
                    "miniapp-staff scope is derived from its session");
        }
        if (channel == Channel.WEB && !platformPath && tenantCode != null) {
            throw new IllegalArgumentException(
                    "staff Web scope must derive tenant from its session");
        }
        if (platformPath && channel != Channel.WEB) {
            throw new IllegalArgumentException(
                    "platform paths are Web-only");
        }
        if (organizationCode != null && platformPath && tenantCode == null) {
            throw new IllegalArgumentException(
                    "platform organization scope requires tenantCode");
        }
    }

    private static String required(String value, String name) {
        String result = optional(value);
        if (result == null) {
            throw new IllegalArgumentException(name + " must not be blank");
        }
        return result;
    }

    private static String optional(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    public enum Channel {
        WEB,
        MINIAPP_STAFF
    }
}
