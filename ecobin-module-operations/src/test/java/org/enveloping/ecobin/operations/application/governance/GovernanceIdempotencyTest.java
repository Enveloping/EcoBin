package org.enveloping.ecobin.operations.application.governance;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class GovernanceIdempotencyTest {

    @Test
    void onlyUuidVersionFourCanBeAnIdempotencyKey() {
        GovernanceIdempotency.requireVersionFour(UUID.randomUUID());
        TargetApiException exception = assertThrows(
                TargetApiException.class,
                () -> GovernanceIdempotency.requireVersionFour(
                        UUID.fromString(
                                "00000000-0000-1000-8000-000000000001")));
        assertEquals(400, exception.status());
    }

    @Test
    void requestDigestIsOrderedAndUnambiguous() {
        String first = GovernanceIdempotency.requestDigest("ab", "c");
        String second = GovernanceIdempotency.requestDigest("a", "bc");
        assertNotEquals(first, second);
        assertEquals(first,
                GovernanceIdempotency.requestDigest("ab", "c"));
    }
}
