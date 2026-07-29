package org.enveloping.ecobin.recycling.infrastructure.delivery;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class JdbcDeliveryBusinessReadRepositorySqlTest {

    @Test
    void everyQueryReadsOnlyRecyclingPrivateTablesWithoutLocks() {
        List<String> sqlStatements = List.of(
                JdbcDeliveryBusinessReadRepository
                        .FIND_CURRENT_DELIVERY_CONFIGURATION_SQL,
                JdbcDeliveryBusinessReadRepository
                        .withPortPlaceholders(
                                JdbcDeliveryBusinessReadRepository
                                        .FIND_CURRENT_BAGS_SQL,
                                2),
                JdbcDeliveryBusinessReadRepository
                        .withPortPlaceholders(
                                JdbcDeliveryBusinessReadRepository
                                        .FIND_CAPACITY_STATES_SQL,
                                2),
                JdbcDeliveryBusinessReadRepository
                        .withPortPlaceholders(
                                JdbcDeliveryBusinessReadRepository
                                        .FIND_ACTIVE_BASELINE_REMEASUREMENTS_SQL,
                                2),
                JdbcDeliveryBusinessReadRepository
                        .withPortPlaceholders(
                                JdbcDeliveryBusinessReadRepository
                                        .FIND_ACTIVE_CLEAN_OPERATIONS_SQL,
                                2),
                JdbcDeliveryBusinessReadRepository
                        .FIND_DELIVERY_ORDER_NO_SQL);

        assertThat(sqlStatements).allSatisfy(sql -> {
            String normalized = sql.toLowerCase(Locale.ROOT);
            assertThat(normalized)
                    .contains("rec_")
                    .doesNotContain(
                            "dev_",
                            "iam_",
                            "fund_",
                            "ops_",
                            "for update");
        });
    }

    @Test
    void activityQueriesUseTheAuthoritativeNonTerminalStates() {
        String baseline =
                JdbcDeliveryBusinessReadRepository
                        .FIND_ACTIVE_BASELINE_REMEASUREMENTS_SQL;
        String clean =
                JdbcDeliveryBusinessReadRepository
                        .FIND_ACTIVE_CLEAN_OPERATIONS_SQL;

        assertThat(baseline)
                .contains("measurement.status = 'PENDING'");
        assertThat(clean)
                .contains(
                        "'PREPARED'",
                        "'EDGE_SAVED'",
                        "'IN_PROGRESS'",
                        "'RECOVERY_REQUIRED'")
                .doesNotContain(
                        "'PRE_UNLOCK_ENDED'",
                        "'COMPLETED'");
    }

    @Test
    void dynamicPortListContainsOnlyBoundParameters() {
        String sql = JdbcDeliveryBusinessReadRepository
                .withPortPlaceholders(
                        JdbcDeliveryBusinessReadRepository
                                .FIND_CAPACITY_STATES_SQL,
                        3);

        assertThat(sql).contains("capacity.port_id IN (?, ?, ?)");
        assertThat(sql).doesNotContain("%s");
    }
}
