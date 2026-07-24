package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.security.JwtSessionClaims;
import org.enveloping.ecobin.framework.security.TrustedSessionRejectedException;
import org.enveloping.ecobin.identity.application.security.LegacyJwtTrustedSessionResolver;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.time.Instant;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertThrows;

class LegacyJwtTrustedSessionResolverRoleTest {

    private final LegacyJwtTrustedSessionResolver resolver =
            new LegacyJwtTrustedSessionResolver(null, null, null);

    @ParameterizedTest
    @ValueSource(ints = {0, 4, 5, 6, 10})
    void rejectsEveryUnknownLegacyRoleBeforeAccessingIdentityStorage(int role) {
        JwtSessionClaims claims = new JwtSessionClaims(
                999L,
                2L,
                role,
                "unknown-role",
                UUID.randomUUID(),
                TrustedAudience.MINIAPP,
                Instant.now());

        assertThrows(TrustedSessionRejectedException.class, () -> resolver.resolve(claims));
    }
}
