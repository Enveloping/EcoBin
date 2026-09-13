package org.enveloping.ecobin.device.application.delivery;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class OfflineDeliveryOccupancyReleaseTest {

    private static final LocalDateTime NOW =
            LocalDateTime.of(2026, 9, 13, 12, 10);

    @Test
    void strictlyMoreThanSixHundredSecondsIsEligible() {
        assertThat(OfflineDeliveryOccupancyReleaseService.eligible(
                new OfflineDeliveryOccupancyReleaseService.Transport(
                        "OFFLINE", NOW.minusSeconds(601)),
                NOW)).isTrue();
        assertThat(OfflineDeliveryOccupancyReleaseService.eligible(
                new OfflineDeliveryOccupancyReleaseService.Transport(
                        "OFFLINE", NOW.minusSeconds(600)),
                NOW)).isFalse();
        assertThat(OfflineDeliveryOccupancyReleaseService.eligible(
                new OfflineDeliveryOccupancyReleaseService.Transport(
                        "OFFLINE", NOW.minusSeconds(599)),
                NOW)).isFalse();
    }

    @Test
    void onlineOrMissingOfflineStartIsNeverEligible() {
        assertThat(OfflineDeliveryOccupancyReleaseService.eligible(
                new OfflineDeliveryOccupancyReleaseService.Transport(
                        "ONLINE", NOW.minusHours(1)), NOW)).isFalse();
        assertThat(OfflineDeliveryOccupancyReleaseService.eligible(
                new OfflineDeliveryOccupancyReleaseService.Transport(
                        "OFFLINE", null), NOW)).isFalse();
    }

    @Test
    void scannerRequiresExactOriginalOccupancyAndDoesNotEndBusiness() {
        String sql = OfflineDeliveryOccupancyReleaseScheduler
                .FIND_CANDIDATES_SQL.toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "OFFLINE_SINCE_AT <",
                        "TIMESTAMPADD(SECOND, -600",
                        "OFFLINE_OCCUPANCY_RELEASED_AT IS NULL",
                        "OCCUPANCY.DELIVERY_SESSION_ID = SESSION.ID")
                .doesNotContain("UPDATE DEV_DELIVERY_SESSION")
                .doesNotContain("OPS_RELIABLE_TASK");
    }
}
