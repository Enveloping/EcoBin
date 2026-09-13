package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

/** Actual migrated MySQL CHECKs; temporary LIKE tables intentionally omit foreign keys/triggers. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_CLEAN_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_median_p1at(?:\\?.*)?")
class CleanMedianMysqlConstraintTest {
    private Connection connection;

    @BeforeEach
    void open() throws Exception {
        connection = DriverManager.getConnection(System.getenv("ECOBIN_CLEAN_MEDIAN_MYSQL_URL"), "root", "");
        assertEquals("ecobin_median_p1at", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        try (var statement = connection.createStatement()) {
            statement.execute("CREATE TEMPORARY TABLE median_result LIKE dev_physical_result");
        }
    }

    @AfterEach
    void close() throws Exception {
        if (connection != null) connection.close();
    }

    @Test
    void finalMedianIsUsableNewBagBaselineWithoutRelabelingItsQuality() throws Exception {
        Map<String, Object> row = normalClean();
        median(row, "clean_final");
        assertEquals(1, insert(row));
        try (var statement = connection.createStatement(); var result = statement.executeQuery(
                "SELECT clean_final_measurement_status,clean_final_weight_value_kind,clean_final_weight_g,"
                        + "clean_final_last_observed_weight_g,clean_new_baseline_weight_g,clean_final_fault_code FROM median_result")) {
            assertTrue(result.next());
            assertEquals("UNSTABLE", result.getString(1));
            assertEquals("TIMEOUT_MEDIAN", result.getString(2));
            assertEquals(1200, result.getLong(3));
            assertNull(result.getObject(4));
            assertEquals(1200, result.getLong(5));
            assertNull(result.getObject(6));
        }
    }


    @ParameterizedTest
    @MethodSource("nativeIdentities")
    void nativeIdentityPreservesBootAndEventBytes(String slot, String mode, long boot, long event) throws Exception {
        Map<String, Object> row = normalClean();
        if (mode.equals("median")) median(row, slot);
        row.put(slot + "_mcu_boot_id", boot);
        row.put(slot + "_mcu_event_sequence", event);
        String uid = nativeUid(boot, event);
        row.put(slot + "_measurement_uid", uid);
        assertEquals(1, insert(row));
        try (var statement = connection.createStatement(); var result = statement.executeQuery(
                "SELECT " + slot + "_measurement_uid FROM median_result")) {
            assertTrue(result.next());
            assertEquals(uid, result.getString(1));
        }
    }

    private static Stream<Arguments> nativeIdentities() {
        return Stream.of("clean_pre", "clean_final").flatMap(slot ->
                Stream.of("mean", "median").flatMap(mode -> Stream.of(
                        Arguments.of(slot, mode, 1L, 1L),
                        Arguments.of(slot, mode, 9007199254740991L, 4294967295L),
                        Arguments.of(slot, mode, 0x0000400080000001L, 2L))));
    }

    @ParameterizedTest
    @MethodSource("contradictoryNativeIdentities")
    void rejectsNativeIdentityThatDisagreesWithItsOwnFields(String slot, String mutation) {
        Map<String, Object> row = normalClean();
        median(row, slot);
        long boot = mutation.startsWith("overlap") ? 0x0000400080000001L : 101L;
        long event = 2;
        String uid = nativeUid(boot, event);
        row.put(slot + "_measurement_uid", uid);
        row.put(slot + "_mcu_boot_id", boot);
        row.put(slot + "_mcu_event_sequence", event);
        switch (mutation) {
            case "boot", "overlapBoot" -> row.put(slot + "_mcu_boot_id", boot + 1);
            case "event", "overlapEvent" -> row.put(slot + "_mcu_event_sequence", event + 1);
            case "uppercase" -> row.put(slot + "_measurement_uid", uid.toUpperCase(java.util.Locale.ROOT));
            case "nullBoot" -> row.put(slot + "_mcu_boot_id", null);
            case "nullEvent" -> row.put(slot + "_mcu_event_sequence", null);
            case "zeroBoot" -> {
                row.put(slot + "_mcu_boot_id", 0L);
                row.put(slot + "_measurement_uid", nativeUid(0, event));
            }
            case "zeroEvent" -> {
                row.put(slot + "_mcu_event_sequence", 0L);
                row.put(slot + "_measurement_uid", nativeUid(boot, 0));
            }
            case "largeBoot" -> {
                row.put(slot + "_mcu_boot_id", 9007199254740992L);
                row.put(slot + "_measurement_uid", nativeUid(9007199254740992L, event));
            }
            case "wrongMagic" -> row.put(slot + "_measurement_uid", uid.replace("45424d31", "45424d32"));
            default -> throw new AssertionError(mutation);
        }
        assertEquals(3819, assertThrows(SQLException.class, () -> insert(row)).getErrorCode());
    }

    private static Stream<Arguments> contradictoryNativeIdentities() {
        return Stream.of("clean_pre", "clean_final").flatMap(slot ->
                Stream.of("boot", "event", "overlapBoot", "overlapEvent", "uppercase", "nullBoot",
                        "nullEvent", "zeroBoot", "zeroEvent", "largeBoot", "wrongMagic")
                        .map(mutation -> Arguments.of(slot, mutation)));
    }

    private static String nativeUid(long boot, long event) {
        String hex = "45424d31" + String.format(java.util.Locale.ROOT, "%016x%08x", boot, event);
        return hex.substring(0, 8) + "-" + hex.substring(8, 12) + "-" + hex.substring(12, 16)
                + "-" + hex.substring(16, 20) + "-" + hex.substring(20);
    }

    private int insert(Map<String, Object> row) throws SQLException {
        String sql = "INSERT INTO median_result (" + String.join(",", row.keySet()) + ") VALUES ("
                + String.join(",", Collections.nCopies(row.size(), "?")) + ")";
        try (var statement = connection.prepareStatement(sql)) {
            int index = 1;
            for (Object value : row.values()) statement.setObject(index++, value);
            return statement.executeUpdate();
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"pre", "final", "both", "legacy"})
    void eitherSlotSupportsMedianAndLegacySingleSampleMean(String slot) throws Exception {
        Map<String, Object> row = normalClean();
        row.put("clean_pre_sample_count", 1);
        row.put("clean_final_sample_count", 1);
        if (slot.equals("pre") || slot.equals("both")) median(row, "clean_pre");
        if (slot.equals("final") || slot.equals("both")) median(row, "clean_final");
        assertEquals(1, insert(row));
    }

    @ParameterizedTest
    @MethodSource("invalidMedianFields")
    void rejectsMissingOrContradictoryMedianEvidence(String slot, String field, Object value) {
        Map<String, Object> row = normalClean();
        median(row, slot);
        row.put(slot + "_" + field, value);
        assertEquals(3819, assertThrows(SQLException.class, () -> insert(row)).getErrorCode());
    }

    private static Stream<Arguments> invalidMedianFields() {
        Object[][] invalid = {
                {"measurement_uid", null}, {"measurement_uid", "not-a-uuid"},
                {"measurement_status", null}, {"measurement_status", "STABLE"},
                {"weight_value_available", null}, {"weight_value_available", false},
                {"weight_g", null}, {"weight_g", -2147483649L}, {"weight_g", 2147483648L},
                {"last_observed_weight_g", 1200}, {"measurement_elapsed_ms", null},
                {"measurement_elapsed_ms", 4999}, {"measurement_elapsed_ms", 5001},
                {"sample_count", null}, {"sample_count", 4}, {"sample_count", 33},
                {"calibration_version", null}, {"calibration_version", 4294967296L},
                {"sensor_health", null}, {"sensor_health", "UNKNOWN"}, {"fault_code", "WEIGHT_UNSTABLE"},
                {"mcu_boot_id", null}, {"mcu_boot_id", 0}, {"mcu_boot_id", 9007199254740992L},
                {"mcu_event_sequence", null}, {"mcu_event_sequence", 0}, {"mcu_event_sequence", 4294967296L}
        };
        return Stream.of("clean_pre", "clean_final").flatMap(slot ->
                Stream.of(invalid).map(pair -> Arguments.of(slot, pair[0], pair[1])));
    }

    @ParameterizedTest
    @ValueSource(longs = {-2147483648L, 0, 2147483647L})
    void preservesSignedBoundaryInsteadOfReplacingItWithZero(long grams) throws Exception {
        Map<String, Object> row = normalClean();
        median(row, "clean_pre");
        median(row, "clean_final");
        row.put("clean_pre_weight_g", grams);
        row.put("clean_final_weight_g", grams);
        row.put("clean_new_baseline_weight_g", grams);
        row.put("clean_pre_sample_count", 5);
        row.put("clean_final_sample_count", 32);
        assertEquals(1, insert(row));
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void newBaselineCannotBeMissingOrDifferentFromUsableMedian(boolean missing) {
        Map<String, Object> row = normalClean();
        median(row, "clean_final");
        row.put("clean_new_baseline_weight_g", missing ? null : 1201);
        assertEquals(3819, assertThrows(SQLException.class, () -> insert(row)).getErrorCode());
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void finalTimeoutCanStillBeArchivedWithoutBaseline(boolean nativeIdentity) throws Exception {
        Map<String, Object> row = normalClean();
        if (nativeIdentity) row.put("clean_final_measurement_uid", nativeUid(101, 2));
        row.put("clean_final_measurement_status", "TIMEOUT");
        row.put("clean_final_weight_g", null);
        row.put("clean_final_weight_value_available", false);
        row.put("clean_final_weight_value_kind", "NONE");
        row.put("clean_final_sample_count", 0);
        row.put("clean_final_sensor_health", "TIMEOUT");
        row.put("clean_final_fault_code", "WEIGHT_TIMEOUT");
        row.put("clean_new_baseline_weight_g", null);
        assertEquals(1, insert(row));
    }

    private static Map<String, Object> normalClean() {
        Map<String, Object> row = new LinkedHashMap<>();
        for (String field : new String[]{"tenant_id", "organization_id", "asset_id", "port_id",
                "edge_event_id", "command_id", "clean_operation_id", "reported_config_version_no"}) row.put(field, 1L);
        row.put("edge_event_type", "CLEAN_COMPLETE");
        row.put("command_type", "START_CLEAN_OPERATION");
        row.put("result_type", "CLEAN");
        row.put("reported_config_content_sha256", new byte[32]);
        row.put("reported_config_mcu_payload_sha256", new byte[32]);
        row.put("created_at", Timestamp.from(Instant.now()));
        measurement(row, "clean_pre", 20000, 1);
        measurement(row, "clean_final", 1200, 2);
        row.put("clean_removed_net_weight_g", 18800);
        row.put("clean_new_baseline_weight_g", 1200);
        row.put("cleaner_completion_confirmed", true);
        row.put("cleaner_physical_close_confirmed", true);
        row.put("clean_action_sequence", 1);
        row.put("clean_lock_power_state", "DEENERGIZED");
        row.put("clean_solenoid_health", "OK");
        row.put("clean_door_inferred_state", "UNKNOWN");
        row.put("clean_door_state_basis", "CLEANER_CONFIRMATION");
        return row;
    }

    private static void measurement(Map<String, Object> row, String prefix, int grams, int sequence) {
        row.put(prefix + "_measurement_uid", UUID.randomUUID().toString());
        row.put(prefix + "_measurement_status", "STABLE");
        row.put(prefix + "_weight_g", grams);
        row.put(prefix + "_weight_value_available", true);
        row.put(prefix + "_weight_value_kind", "STABLE_WINDOW_MEAN");
        row.put(prefix + "_measurement_elapsed_ms", 1200);
        row.put(prefix + "_sample_count", 5);
        row.put(prefix + "_calibration_version", 4);
        row.put(prefix + "_sensor_health", "OK");
        row.put(prefix + "_mcu_boot_id", 101);
        row.put(prefix + "_mcu_event_sequence", sequence);
    }

    private static void median(Map<String, Object> row, String prefix) {
        row.put(prefix + "_measurement_status", "UNSTABLE");
        row.put(prefix + "_weight_value_kind", "TIMEOUT_MEDIAN");
        row.put(prefix + "_measurement_elapsed_ms", 5000);
        row.put(prefix + "_sample_count", 20);
    }
}
