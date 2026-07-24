package org.enveloping.ecobin.framework.security;

import org.enveloping.ecobin.framework.context.TrustedExecutionContext;

public record ResolvedTrustedSession(
        TrustedExecutionContext context,
        long legacyPrincipalId,
        long legacyTenantId,
        int legacyRole,
        String authenticationName) {
}
