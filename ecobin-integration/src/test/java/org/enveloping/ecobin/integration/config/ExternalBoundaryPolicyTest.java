package org.enveloping.ecobin.integration.config;

import org.junit.jupiter.api.Test;

import static org.enveloping.ecobin.integration.config.ExternalAdapterModeProperties.Mode.FAKE;
import static org.enveloping.ecobin.integration.config.ExternalAdapterModeProperties.Mode.REAL;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ExternalBoundaryPolicyTest {

    @Test
    void acceptsCredentialFreeFakeModeWithInboundBlocked() {
        var result = ExternalBoundaryPolicy.verify(snapshot(
                FAKE, true, false, false, false, false, false, false));

        assertEquals(FAKE, result.mode());
        assertTrue(result.inboundBlocked());
    }

    @Test
    void rejectsAnyRealCredentialOrConsumerInFakeMode() {
        assertRejected(snapshot(
                FAKE, true, false, true, false, false, false, false));
        assertRejected(snapshot(
                FAKE, true, false, false, true, false, false, false));
        assertRejected(snapshot(
                FAKE, true, false, false, false, true, false, false));
        assertRejected(snapshot(
                FAKE, true, false, false, false, false, true, false));
        assertRejected(snapshot(
                FAKE, true, false, false, false, false, false, true));
    }

    @Test
    void onlyLegacyTestsMayDisableFakeInboundBlock() {
        assertRejected(snapshot(
                FAKE, false, false, false, false, false, false, false));

        var result = ExternalBoundaryPolicy.verify(snapshot(
                FAKE, false, true, false, false, false, false, false));
        assertEquals(FAKE, result.mode());
    }

    @Test
    void realModeFailsUnlessEveryChannelIsExplicitlyConfigured() {
        assertRejected(snapshot(
                REAL, true, false, true, true, true, true, false));

        var result = ExternalBoundaryPolicy.verify(snapshot(
                REAL, true, false, true, true, true, true, true));
        assertEquals(REAL, result.mode());
    }

    private static ExternalBoundaryPolicy.Snapshot snapshot(
            ExternalAdapterModeProperties.Mode mode,
            boolean blockInbound,
            boolean legacyTestRuntime,
            boolean subscriptionEnabled,
            boolean inboundConfigured,
            boolean outboundConfigured,
            boolean cosConfigured,
            boolean wechatConfigured) {
        return new ExternalBoundaryPolicy.Snapshot(
                mode,
                blockInbound,
                legacyTestRuntime,
                subscriptionEnabled,
                inboundConfigured,
                outboundConfigured,
                cosConfigured,
                wechatConfigured);
    }

    private static void assertRejected(
            ExternalBoundaryPolicy.Snapshot snapshot) {
        assertThrows(
                ExternalBoundaryException.class,
                () -> ExternalBoundaryPolicy.verify(snapshot));
    }
}
