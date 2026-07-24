package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedExecutionContext;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class TrustedExecutionContextShapeTest {

    private static final UUID PRINCIPAL = UUID.randomUUID();
    private static final UUID TENANT = UUID.randomUUID();
    private static final UUID ORGANIZATION = UUID.randomUUID();
    private static final UUID SESSION = UUID.randomUUID();

    @Test
    void acceptsOnlyDefinedPrincipalAudienceAndScopeShapes() {
        assertDoesNotThrow(() -> context(
                TrustedPrincipalKind.PLATFORM_ADMIN, TrustedAudience.WEB_PLATFORM, null, null));
        assertDoesNotThrow(() -> context(
                TrustedPrincipalKind.STAFF_ACCOUNT, TrustedAudience.WEB_STAFF, TENANT, null));
        assertDoesNotThrow(() -> context(
                TrustedPrincipalKind.STAFF_ACCOUNT, TrustedAudience.MINIAPP_STAFF, TENANT, ORGANIZATION));
        assertDoesNotThrow(() -> context(
                TrustedPrincipalKind.ORGANIZATION_USER, TrustedAudience.MINIAPP, TENANT, ORGANIZATION));

        assertThrows(IllegalArgumentException.class, () -> context(
                TrustedPrincipalKind.PLATFORM_ADMIN, TrustedAudience.WEB_PLATFORM, TENANT, null));
        assertThrows(IllegalArgumentException.class, () -> context(
                TrustedPrincipalKind.PLATFORM_ADMIN, TrustedAudience.MINIAPP, null, null));
        assertThrows(IllegalArgumentException.class, () -> context(
                TrustedPrincipalKind.STAFF_ACCOUNT, TrustedAudience.WEB_STAFF, null, null));
        assertThrows(IllegalArgumentException.class, () -> context(
                TrustedPrincipalKind.STAFF_ACCOUNT, TrustedAudience.MINIAPP_STAFF, TENANT, null));
        assertThrows(IllegalArgumentException.class, () -> context(
                TrustedPrincipalKind.ORGANIZATION_USER, TrustedAudience.MINIAPP, TENANT, null));
        assertThrows(IllegalArgumentException.class, () -> context(
                TrustedPrincipalKind.ORGANIZATION_USER, TrustedAudience.WEB_STAFF, TENANT, ORGANIZATION));
    }

    private static TrustedExecutionContext context(
            TrustedPrincipalKind kind,
            TrustedAudience audience,
            UUID tenant,
            UUID organization) {
        return new TrustedExecutionContext(
                kind, PRINCIPAL, audience, tenant, organization, SESSION, 0, "request");
    }
}
