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

/** Actual migrated MySQL CHECK/unique constraints; temporary LIKE tables omit foreign keys. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_median_p1as(?:\\?.*)?")
class DeliveryMedianMysqlConstraintTest {
    private Connection connection;

    @BeforeEach
    void openTemporaryConstraintCopy() throws Exception {
        connection = DriverManager.getConnection(System.getenv("ECOBIN_MEDIAN_MYSQL_URL"),
                System.getenv().getOrDefault("ECOBIN_MEDIAN_MYSQL_USER", "root"),
                System.getenv().getOrDefault("ECOBIN_MEDIAN_MYSQL_PASSWORD", ""));
        assertEquals("ecobin_median_p1as", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        connection.createStatement().execute("CREATE TEMPORARY TABLE median_result LIKE dev_physical_result");
    }

    @AfterEach
    void close() throws Exception {
        if (connection != null) connection.close();
    }

    @Test
    void completeDeliveryStoresUsableMedianWithoutAStableRelabelOrFault() throws Exception {
        Map<String, Object> row = normalDelivery();
        median(row, "delivery_post");
        assertEquals(1, insert(row));
        try (var result = connection.createStatement().executeQuery(
                "SELECT delivery_post_measurement_status, delivery_post_weight_value_kind, "
                        + "delivery_post_weight_g, delivery_post_fault_code, delivery_net_weight_g FROM median_result")) {
            assertTrue(result.next());
            assertEquals("UNSTABLE", result.getString(1));
            assertEquals("TIMEOUT_MEDIAN", result.getString(2));
            assertEquals(12500, result.getLong(3));
            assertNull(result.getString(4));
            assertEquals(500, result.getLong(5));
        }
    }


    @ParameterizedTest
    @MethodSource("nativeIdentities")
    void nativeIdentityPreservesBootAndEventBytes(String slot, String mode, long boot, long event) throws Exception {
        Map<String, Object> row = normalDelivery();
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
        return Stream.of("delivery_pre", "delivery_post").flatMap(slot ->
                Stream.of("mean", "median").flatMap(mode -> Stream.of(
                        Arguments.of(slot, mode, 1L, 1L),
                        Arguments.of(slot, mode, 9007199254740991L, 4294967295L),
                        Arguments.of(slot, mode, 0x0000400080000001L, 2L))));
    }

    @ParameterizedTest
    @MethodSource("contradictoryNativeIdentities")
    void rejectsNativeIdentityThatDisagreesWithItsOwnFields(String slot, String mutation) {
        Map<String, Object> row = normalDelivery();
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
        return Stream.of("delivery_pre", "delivery_post").flatMap(slot ->
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
    @ValueSource(strings = {"pre", "post", "both", "legacy"})
    void acceptsEitherMedianSlotAndPreservesLegacySingleSampleMean(String slot) throws Exception {
        Map<String, Object> row = normalDelivery();
        row.put("delivery_pre_sample_count", 1);
        row.put("delivery_post_sample_count", 1);
        if (slot.equals("pre") || slot.equals("both")) median(row, "delivery_pre");
        if (slot.equals("post") || slot.equals("both")) median(row, "delivery_post");
        assertEquals(1, insert(row));
    }

    @ParameterizedTest
    @MethodSource("invalidMedianFields")
    void rejectsIncompleteOrContradictoryMedianInEitherSlot(String slot, String field, Object value) {
        Map<String, Object> row = normalDelivery();
        median(row, slot);
        row.put(slot + "_" + field, value);
        SQLException failure = assertThrows(SQLException.class, () -> insert(row));
        assertEquals(3819, failure.getErrorCode(), "must fail a CHECK, not fixture SQL");
    }

    private static Stream<Arguments> invalidMedianFields() {
        Object[][] invalid = {
                {"measurement_uid", null}, {"measurement_uid", "not-a-uuid"},
                {"measurement_status", null}, {"measurement_status", "STABLE"},
                {"weight_value_available", null}, {"weight_value_available", false},
                {"weight_g", null}, {"weight_g", -2147483649L}, {"weight_g", 2147483648L},
                {"last_observed_weight_g", 12500},
                {"measurement_elapsed_ms", null}, {"measurement_elapsed_ms", 4999},
                {"measurement_elapsed_ms", 5001},
                {"sample_count", null}, {"sample_count", 4}, {"sample_count", 33},
                {"calibration_version", null}, {"calibration_version", 4294967296L},
                {"sensor_health", null}, {"sensor_health", "UNKNOWN"},
                {"fault_code", "WEIGHT_UNSTABLE"},
                {"mcu_boot_id", null}, {"mcu_boot_id", 0}, {"mcu_boot_id", 9007199254740992L},
                {"mcu_event_sequence", null}, {"mcu_event_sequence", 0},
                {"mcu_event_sequence", 4294967296L}
        };
        return Stream.of("delivery_pre", "delivery_post").flatMap(slot ->
                Stream.of(invalid).map(pair -> Arguments.of(slot, pair[0], pair[1])));
    }

    @ParameterizedTest
    @ValueSource(longs = {-2147483648L, 0, 2147483647L})
    void storesSignedMedianBoundaryWithoutManufacturingZero(long grams) throws Exception {
        Map<String, Object> row = normalDelivery();
        median(row, "delivery_pre");
        median(row, "delivery_post");
        row.put("delivery_pre_weight_g", grams);
        row.put("delivery_post_weight_g", grams);
        row.put("delivery_net_weight_g", 0);
        row.put("delivery_pre_sample_count", 5);
        row.put("delivery_post_sample_count", 32);
        assertEquals(1, insert(row));
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void keepsTerminalWeightFailureWithoutInventingFinalOrNetWeight(boolean nativeIdentity) throws Exception {
        Map<String, Object> row = normalDelivery();
        if (nativeIdentity) row.put("delivery_post_measurement_uid", nativeUid(101, 2));
        row.put("delivery_post_measurement_status", "TIMEOUT");
        row.put("delivery_post_weight_g", null);
        row.put("delivery_post_weight_value_available", false);
        row.put("delivery_post_weight_value_kind", "NONE");
        row.put("delivery_post_sample_count", 0);
        row.put("delivery_post_sensor_health", "TIMEOUT");
        row.put("delivery_post_fault_code", "WEIGHT_TIMEOUT");
        row.put("delivery_net_weight_g", null);
        row.put("delivery_completion_reason", "TERMINAL_WEIGHT_FAILURE");
        assertEquals(1, insert(row));
    }

    private static Map<String, Object> normalDelivery() {
        Map<String, Object> row = new LinkedHashMap<>();
        for (String field : new String[]{"tenant_id", "organization_id", "asset_id", "port_id",
                "edge_event_id", "command_id", "delivery_session_id", "reported_config_version_no"}) {
            row.put(field, 1L);
        }
        row.put("edge_event_type", "DELIVERY_COMPLETE");
        row.put("command_type", "START_DELIVERY_SESSION");
        row.put("result_type", "DELIVERY");
        row.put("reported_config_content_sha256", new byte[32]);
        row.put("reported_config_mcu_payload_sha256", new byte[32]);
        row.put("created_at", Timestamp.from(Instant.now()));
        measurement(row, "delivery_pre", 12000, 1);
        measurement(row, "delivery_post", 12500, 2);
        row.put("delivery_net_weight_g", 500L);
        row.put("delivery_final_door_command", "CLOSE");
        row.put("delivery_final_door_output_status", "COMMAND_DISPATCHED");
        row.put("delivery_final_door_physical_state_basis", "NOT_OBSERVABLE");
        row.put("delivery_completion_reason", "USER_ENDED");
        row.put("delivery_manual_review_required", false);
        row.put("negative_weight_anomaly", false);
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
