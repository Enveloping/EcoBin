package org.enveloping.ecobin.recycling.application.clean;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class StartCleanOperationServiceSqlTest {

    @Test
    void immutableCleanStartFactsUsePlainReads() {
        List<String> immutableSql = List.of(
                StartCleanOperationService
                        .LOAD_LATEST_CONFIGURATION_SQL,
                StartCleanOperationService.LOAD_PORT_SQL,
                StartCleanOperationService
                        .LOAD_PORT_CONFIGURATION_SQL,
                StartCleanOperationService.LOAD_BAG_SQL,
                StartCleanOperationService.LOAD_CURRENT_BAG_SQL);

        assertThat(immutableSql)
                .allSatisfy(sql ->
                        assertThat(sql.toUpperCase(Locale.ROOT))
                                .doesNotContain("FOR UPDATE"));
    }

    @Test
    void currentBagLockTargetsOnlyTheMutableOccupancySlot() {
        String sql = StartCleanOperationService
                .LOCK_CURRENT_BAG_OCCUPANCY_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(sql)
                .contains("from rec_bag_current_occupancy")
                .contains("for update")
                .doesNotContain("join rec_bag");
    }
}
