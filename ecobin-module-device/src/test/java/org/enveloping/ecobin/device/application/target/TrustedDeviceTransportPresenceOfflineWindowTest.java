package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class TrustedDeviceTransportPresenceOfflineWindowTest {

    private static final LocalDateTime FIRST =
            LocalDateTime.of(2026, 9, 13, 12, 0);
    private static final LocalDateTime LATER = FIRST.plusMinutes(2);

    @Test
    void firstOfflineUsesTrustedBackendReceiveTime() {
        var current = new TrustedDeviceTransportPresenceService.TransportRow(
                1L, "ONLINE", FIRST.minusMinutes(1), FIRST.minusMinutes(1), null);

        assertThat(TrustedDeviceTransportPresenceService.offlineSinceAt(
                "OFFLINE", FIRST, current)).isEqualTo(FIRST);
    }

    @Test
    void repeatedOfflinePreservesFirstContinuousOfflineTime() {
        var current = new TrustedDeviceTransportPresenceService.TransportRow(
                1L, "OFFLINE", FIRST, FIRST, FIRST);

        assertThat(TrustedDeviceTransportPresenceService.offlineSinceAt(
                "OFFLINE", LATER, current)).isEqualTo(FIRST);
    }

    @Test
    void onlineClearsContinuousOfflineTime() {
        var current = new TrustedDeviceTransportPresenceService.TransportRow(
                1L, "OFFLINE", FIRST, FIRST, FIRST);

        assertThat(TrustedDeviceTransportPresenceService.offlineSinceAt(
                "ONLINE", LATER, current)).isNull();
    }
}
