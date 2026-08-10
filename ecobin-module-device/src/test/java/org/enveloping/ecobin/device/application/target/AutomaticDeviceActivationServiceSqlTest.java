package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.Locale;
import java.util.UUID;

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
    void keepsEveryPortVisibleWhenOneRelatedFactIsMissing() {
        assertThat(upper(
                AutomaticDeviceActivationService
                        .LOAD_INITIAL_BASELINE_FACTS_SQL))
                .contains(
                        "LEFT JOIN DEV_FACTORY_INSTALLED_BAG",
                        "LEFT JOIN REC_BAG_CURRENT_OCCUPANCY",
                        "LEFT JOIN REC_BAG CURRENT_BAG",
                        "LEFT JOIN REC_PORT_CAPACITY_STATE",
                        "LEFT JOIN DEV_PORT_CONFIG_SNAPSHOT");
    }

    @Test
    void namesTheFirstMissingPortFactInsteadOfHidingThePort() {
        UUID bagUid = UUID.fromString(
                "00000000-0000-0000-0000-000000000001");

        assertThat(AutomaticDeviceActivationService
                .missingAutomaticBaselineFact(
                        null, 2L, 3L, 3L, bagUid, 4L, 5L))
                .isEqualTo("FACTORY_INSTALLATION");
        assertThat(AutomaticDeviceActivationService
                .missingAutomaticBaselineFact(
                        1L, null, 3L, 3L, bagUid, 4L, 5L))
                .isEqualTo("FACTORY_BAG");
        assertThat(AutomaticDeviceActivationService
                .missingAutomaticBaselineFact(
                        1L, 2L, null, 3L, bagUid, 4L, 5L))
                .isEqualTo("CURRENT_BAG_OCCUPANCY");
        assertThat(AutomaticDeviceActivationService
                .missingAutomaticBaselineFact(
                        1L, 2L, 3L, null, bagUid, 4L, 5L))
                .isEqualTo("CURRENT_BAG");
        assertThat(AutomaticDeviceActivationService
                .missingAutomaticBaselineFact(
                        1L, 2L, 3L, 3L, bagUid, null, 5L))
                .isEqualTo("CAPACITY_STATE");
        assertThat(AutomaticDeviceActivationService
                .missingAutomaticBaselineFact(
                        1L, 2L, 3L, 3L, bagUid, 4L, null))
                .isEqualTo("PORT_CONFIGURATION_SNAPSHOT");
        assertThat(AutomaticDeviceActivationService
                .missingAutomaticBaselineFact(
                        1L, 2L, 3L, 3L, bagUid, 4L, 5L))
                .isNull();
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

    @Test
    void selectsTheNewestVersionBeforeCheckingItsApplication() {
        String sql = Arrays.stream(
                        AutomaticDeviceActivationService.class
                                .getDeclaredFields())
                .filter(field -> Modifier.isStatic(
                        field.getModifiers()))
                .filter(field -> field.getType() == String.class)
                .map(AutomaticDeviceActivationServiceSqlTest::readString)
                .map(AutomaticDeviceActivationServiceSqlTest::upper)
                .filter(statement -> statement.contains(
                        "FROM DEV_CONFIG_VERSION VERSION"))
                .findFirst()
                .orElse("");

        assertThat(sql)
                .contains(
                        "LEFT JOIN DEV_CONFIG_APPLICATION APPLICATION",
                        "ORDER BY VERSION.VERSION_NO DESC",
                        "LIMIT 1");
    }

    private static String readString(Field field) {
        try {
            field.setAccessible(true);
            return (String) field.get(null);
        } catch (IllegalAccessException exception) {
            throw new AssertionError(exception);
        }
    }

    private static String upper(String sql) {
        return sql.toUpperCase(Locale.ROOT);
    }
}
