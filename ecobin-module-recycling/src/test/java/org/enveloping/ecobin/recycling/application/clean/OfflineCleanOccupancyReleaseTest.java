package org.enveloping.ecobin.recycling.application.clean;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class OfflineCleanOccupancyReleaseTest {

    private static final LocalDateTime NOW =
            LocalDateTime.of(2026, 9, 13, 12, 10);

    @Test
    void strictlyMoreThanSixHundredSecondsIsEligible() {
        assertThat(OfflineCleanOccupancyReleaseService.eligible(
                new OfflineCleanOccupancyReleaseService.Transport(
                        "OFFLINE", NOW.minusSeconds(601)),
                NOW)).isTrue();
        assertThat(OfflineCleanOccupancyReleaseService.eligible(
                new OfflineCleanOccupancyReleaseService.Transport(
                        "OFFLINE", NOW.minusSeconds(600)),
                NOW)).isFalse();
    }

    @Test
    void scannerKeepsTheReservedBagAndRequiresExactCleanOccupancy() {
        String sql = OfflineCleanOccupancyReleaseScheduler
                .FIND_CANDIDATES_SQL.toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "OFFLINE_SINCE_AT <",
                        "TIMESTAMPADD(SECOND, -600",
                        "OFFLINE_OCCUPANCY_RELEASED_AT IS NULL",
                        "OCCUPANCY.CLEAN_OPERATION_ID = OPERATION.ID")
                .doesNotContain("REC_BAG_CURRENT_OCCUPANCY")
                .doesNotContain("OPS_RELIABLE_TASK");
    }
}
