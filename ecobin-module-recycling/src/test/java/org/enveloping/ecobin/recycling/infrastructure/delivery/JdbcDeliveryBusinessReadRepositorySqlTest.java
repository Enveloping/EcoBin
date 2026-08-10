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

    @Test
    void displayedFullStateMustBelongToCurrentBag() {
        String sql = JdbcDeliveryBusinessReadRepository
                .FIND_CAPACITY_STATES_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(sql)
                .contains("left join rec_bag_current_occupancy")
                .contains("capacity.current_bag_id = occupancy.bag_id")
                .contains("then 'full'")
                .contains("else 'not_full'");
    }

    @Test
    void validBaselineMustBelongToTheCurrentOccupiedBag() {
        String sql = JdbcDeliveryBusinessReadRepository
                .FIND_CAPACITY_STATES_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(sql)
                .contains("left join rec_port_weight_baseline")
                .contains("capacity.current_baseline_id = baseline.id")
                .contains("capacity.current_bag_id = occupancy.bag_id")
                .contains("baseline.bag_id = occupancy.bag_id")
                .contains("baseline.baseline_weight_g = capacity.current_baseline_weight_g");
    }

    @Test
    void displayedFullnessBelongsToCurrentBagWithoutRequiringWeightBaseline() {
        String sql = JdbcDeliveryBusinessReadRepository
                .FIND_CAPACITY_STATES_SQL
                .toLowerCase(Locale.ROOT);
        int projectionStart = sql.indexOf("end as baseline_state,");
        int projectionEnd = sql.indexOf(
                "end as displayed_fullness_percent");
        String fullnessProjection = sql.substring(
                projectionStart,
                projectionEnd);

        assertThat(fullnessProjection)
                .contains("then capacity.displayed_fullness_percent")
                .contains("else null")
                .contains("capacity.current_bag_id = occupancy.bag_id")
                .doesNotContain("baseline.");
    }
}
