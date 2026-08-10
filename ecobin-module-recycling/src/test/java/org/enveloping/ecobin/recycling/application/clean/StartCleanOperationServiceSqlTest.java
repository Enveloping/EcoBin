package org.enveloping.ecobin.recycling.application.clean;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

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
                StartCleanOperationService.LOAD_CURRENT_BAG_SQL,
                StartCleanOperationService.LOAD_CURRENT_BASELINE_SQL);

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

    @Test
    void frozenBaselineQueriesItsBagGeneration() {
        String sql = Arrays.stream(
                        StartCleanOperationService.class
                                .getDeclaredFields())
                .filter(field -> Modifier.isStatic(
                        field.getModifiers()))
                .filter(field -> field.getType() == String.class)
                .map(StartCleanOperationServiceSqlTest::readString)
                .reduce("", (left, right) -> left + "\n" + right)
                .toLowerCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "from rec_port_capacity_state",
                        "current_bag_id",
                        "current_baseline_id",
                        "from rec_port_weight_baseline",
                        "bag_id",
                        "baseline_weight_g");
    }

    @Test
    void freezesOnlyTheCurrentBagsExactBaselineAsTrusted() {
        var oldBag = new StartCleanOperationService.CurrentBag(
                41L,
                UUID.fromString(
                        "10000000-0000-4000-8000-000000000041"),
                "BAG-41");
        var exactCapacity = new StartCleanOperationService.Capacity(
                41L,
                "VALID",
                51L,
                100L);
        var exactBaseline =
                new StartCleanOperationService.WeightBaseline(
                        51L,
                        41L,
                        100L);
        var oldGenerationCapacity =
                new StartCleanOperationService.Capacity(
                        40L,
                        "VALID",
                        50L,
                        90L);
        var oldGenerationBaseline =
                new StartCleanOperationService.WeightBaseline(
                        50L,
                        40L,
                        90L);

        assertThat(StartCleanOperationService.baseline(
                oldBag,
                exactCapacity,
                exactBaseline))
                .extracting(
                        StartCleanOperationService.Baseline::state,
                        StartCleanOperationService.Baseline::id,
                        StartCleanOperationService.Baseline::weightGrams)
                .containsExactly("TRUSTED", 51L, 100L);
        assertThat(StartCleanOperationService.baseline(
                oldBag,
                oldGenerationCapacity,
                oldGenerationBaseline))
                .extracting(
                        StartCleanOperationService.Baseline::state,
                        StartCleanOperationService.Baseline::id,
                        StartCleanOperationService.Baseline::weightGrams)
                .containsExactly("UNTRUSTED", null, null);
    }

    @Test
    void configurationProofDoesNotUseADiagnosticSafetySnapshot() {
        String sql = StartCleanOperationService
                .LOAD_LATEST_CONFIGURATION_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "runtime.applied_config_version_no = config.version_no",
                        "runtime.applied_config_content_sha256 = config.content_sha256",
                        "runtime.applied_mcu_payload_sha256 = config.mcu_payload_sha256")
                .doesNotContain("runtime.safety_status");
    }

    private static String readString(Field field) {
        try {
            field.setAccessible(true);
            return (String) field.get(null);
        } catch (IllegalAccessException exception) {
            throw new AssertionError(exception);
        }
    }
}
