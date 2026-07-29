package org.enveloping.ecobin.recycling.application.delivery;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class ApplyDeliveryCompleteServiceSqlTest {

    @Test
    void locksBagOccupancyBeforePlainImmutableBagRead() {
        assertThat(upper(
                ApplyDeliveryCompleteService
                        .LOCK_CURRENT_BAG_OCCUPANCY_SQL))
                .contains(
                        "FROM REC_BAG_CURRENT_OCCUPANCY",
                        "FOR UPDATE");
        assertThat(upper(
                ApplyDeliveryCompleteService.LOAD_FROZEN_BAG_SQL))
                .contains("FROM REC_BAG")
                .doesNotContain("FOR UPDATE");
    }

    private static String upper(String sql) {
        return sql.toUpperCase(Locale.ROOT);
    }
}
