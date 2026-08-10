package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AutomaticDeviceActivationServiceSqlTest {

    @Test
    void locksMutableCapacityButPlainReadsImmutableBaselineFacts() {
        assertThat(upper(
                AutomaticDeviceActivationService
                        .LOCK_INITIAL_BASELINE_CAPACITY_SQL))
                .contains(
                        "FROM REC_PORT_CAPACITY_STATE",
                        "ORDER BY PORT_ID",
                        "FOR UPDATE")
                .doesNotContain(" JOIN ");
        assertThat(upper(
                AutomaticDeviceActivationService
                        .LOAD_INITIAL_BASELINE_FACTS_SQL))
                .contains(
                        "FROM DEV_PORT PORT",
                        "JOIN DEV_FACTORY_INSTALLED_BAG",
                        "JOIN REC_PORT_CAPACITY_STATE")
                .doesNotContain("FOR UPDATE");
    }

    @Test
    void readsTheCurrentBagBaselineWithoutRequiringTheFactoryBagToRemainBound() {
        assertThat(upper(
                AutomaticDeviceActivationService
                        .LOAD_INITIAL_BASELINE_FACTS_SQL))
                .contains(
                        "JOIN REC_BAG CURRENT_BAG",
                        "CURRENT_BAG.ID = OCCUPANCY.BAG_ID",
                        "LEFT JOIN REC_PORT_WEIGHT_BASELINE CURRENT_BASELINE",
                        "CURRENT_BASELINE.ID = CAPACITY.CURRENT_BASELINE_ID")
                .doesNotContain(
                        "BAG.BAG_CODE = FACTORY_BAG.BAG_CODE",
                        "OCCUPANCY.BAG_ID = BAG.ID");
    }

    @Test
    void replacementBagNeverRestartsTheHistoricalFactoryMeasurement() {
        assertThat(AutomaticDeviceActivationService
                .requiresAutomaticInitialBaseline(
                        10L, 20L, 20L,
                        "INVALID", null, null))
                .isFalse();
    }

    @Test
    void validBaselineMustBelongToTheCurrentBag() {
        assertThatThrownBy(() -> AutomaticDeviceActivationService
                .requiresAutomaticInitialBaseline(
                        10L, 20L, 20L,
                        "VALID", 30L, 10L))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("does not match current bag");
    }

    @Test
    void factoryBagWithoutBaselineStillStartsAutomaticMeasurement() {
        assertThat(AutomaticDeviceActivationService
                .requiresAutomaticInitialBaseline(
                        10L, 10L, 10L,
                        "UNINITIALIZED", null, null))
                .isTrue();
    }

    private static String upper(String sql) {
        return sql.toUpperCase(Locale.ROOT);
    }
}
