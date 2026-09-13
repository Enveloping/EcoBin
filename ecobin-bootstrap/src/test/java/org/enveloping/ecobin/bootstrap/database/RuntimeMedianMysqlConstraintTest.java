package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

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

/** Migrated MySQL constraints; temporary LIKE copies omit foreign keys and triggers. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_RUNTIME_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_median_p1au(?:\\?.*)?")
class RuntimeMedianMysqlConstraintTest {
    private Connection connection;

    @BeforeEach
    void open() throws Exception {
        connection = DriverManager.getConnection(System.getenv("ECOBIN_RUNTIME_MEDIAN_MYSQL_URL"), "root", "");
        assertEquals("ecobin_median_p1au", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        try (var statement = connection.createStatement()) {
            statement.execute("CREATE TEMPORARY TABLE median_runtime LIKE dev_port_runtime_state");
        }
    }

    @AfterEach
    void close() throws Exception { if (connection != null) connection.close(); }

    @Test
    void storesMedianAndItsActualMeasurementIdentityWithoutFaultRelabeling() throws Exception {
        Map<String, Object> row = medianRuntime();
        assertEquals(1, insert(row));
        try (var statement = connection.createStatement(); var result = statement.executeQuery(
                "SELECT weight_measurement_status,weight_value_kind,reported_weight_grams,weight_fault_code,"
                        + "weight_mcu_boot_id,weight_mcu_event_sequence FROM median_runtime")) {
            assertTrue(result.next());
            assertEquals("UNSTABLE", result.getString(1));
            assertEquals("TIMEOUT_MEDIAN", result.getString(2));
            assertEquals(13250, result.getLong(3));
            assertNull(result.getObject(4));
            assertEquals(101, result.getLong(5));
            assertEquals(51, result.getLong(6));
        }
    }

    @ParameterizedTest
    @MethodSource("invalidMedian")
    void databaseRejectsMalformedMedianIncludingSqlNulls(String field, Object value) {
        Map<String, Object> row = medianRuntime();
        row.put(field, value);
        assertThrows(SQLException.class, () -> insert(row));
    }

    static Stream<Arguments> invalidMedian() {
        return Stream.of(
                Arguments.of("weight_measurement_status", "STABLE"), Arguments.of("weight_measurement_status", null),
                Arguments.of("weight_value_available", false), Arguments.of("weight_value_available", null),
                Arguments.of("reported_weight_grams", null), Arguments.of("reported_weight_grams", 2147483648L),
                Arguments.of("reported_weight_grams", -2147483649L),
                Arguments.of("weight_measurement_elapsed_ms", 4999), Arguments.of("weight_measurement_elapsed_ms", 5001),
                Arguments.of("weight_measurement_elapsed_ms", null), Arguments.of("weight_sample_count", 4),
                Arguments.of("weight_sample_count", 33), Arguments.of("weight_sample_count", null),
                Arguments.of("weight_sensor_health", "TIMEOUT"), Arguments.of("weight_sensor_health", null),
                Arguments.of("weight_fault_code", "WEIGHT_UNSTABLE"), Arguments.of("weight_fault_code", ""),
                Arguments.of("calibration_version", -1), Arguments.of("calibration_version", 4294967296L),
                Arguments.of("calibration_version", null), Arguments.of("weight_mcu_boot_id", 0),
                Arguments.of("weight_mcu_boot_id", 9007199254740992L), Arguments.of("weight_mcu_boot_id", null),
                Arguments.of("weight_mcu_event_sequence", 0), Arguments.of("weight_mcu_event_sequence", 4294967296L),
                Arguments.of("weight_mcu_event_sequence", null), Arguments.of("weight_measurement_uid", null),
                Arguments.of("weight_measurement_uid", "not-a-uuid"),
                Arguments.of("weight_measurement_uid", "89500000-0000-1000-8000-000000000001"),
                Arguments.of("trusted_runtime_sequence", null), Arguments.of("trusted_runtime_edge_event_id", null),
                Arguments.of("trusted_runtime_edge_event_type", null), Arguments.of("fullness_sensor_kind", null));
    }

    @ParameterizedTest
    @MethodSource("validBounds")
    void databaseAcceptsExactNumericBoundaries(long grams, int count, long calibration, long boot, long sequence) throws Exception {
        Map<String, Object> row = medianRuntime();
        row.put("reported_weight_grams", grams);
        row.put("weight_sample_count", count);
        row.put("calibration_version", calibration);
        row.put("weight_mcu_boot_id", boot);
        row.put("weight_mcu_event_sequence", sequence);
        assertEquals(1, insert(row));
    }

    static Stream<Arguments> validBounds() {
        return Stream.of(Arguments.of(-2147483648L, 5, 0L, 1L, 1L),
                Arguments.of(2147483647L, 32, 4294967295L, 9007199254740991L, 4294967295L));
    }

    @Test
    void legacyMeanMayLackHistoricalMeasurementIdentity() throws Exception {
        Map<String, Object> row = medianRuntime();
        row.put("weight_measurement_status", "STABLE");
        row.put("weight_value_kind", "STABLE_WINDOW_MEAN");
        row.put("weight_sample_count", 1);
        row.put("weight_measurement_elapsed_ms", 0);
        row.put("weight_mcu_boot_id", null);
        row.put("weight_mcu_event_sequence", null);
        assertEquals(1, insert(row));
    }

    @Test
    void genuineUnavailableTimeoutRetainsNullWeightAndFaultCode() throws Exception {
        Map<String, Object> row = medianRuntime();
        row.put("weight_measurement_status", "TIMEOUT");
        row.put("weight_value_kind", "NONE");
        row.put("weight_value_available", false);
        row.put("reported_weight_grams", null);
        row.put("weight_sample_count", 0);
        row.put("weight_sensor_health", "TIMEOUT");
        row.put("weight_fault_code", "WEIGHT_TIMEOUT");
        assertEquals(1, insert(row));
    }

    private int insert(Map<String, Object> row) throws SQLException {
        String sql = "INSERT INTO median_runtime (" + String.join(",", row.keySet()) + ") VALUES ("
                + String.join(",", Collections.nCopies(row.size(), "?")) + ")";
        try (var statement = connection.prepareStatement(sql)) {
            int index = 1;
            for (Object value : row.values()) statement.setObject(index++, value);
            return statement.executeUpdate();
        }
    }

    private static Map<String, Object> medianRuntime() {
        Map<String, Object> row = new LinkedHashMap<>();
        for (String field : new String[]{"tenant_id", "organization_id", "asset_id", "port_id",
                "trusted_runtime_edge_event_id", "trusted_runtime_sequence"}) row.put(field, 1L);
        row.put("trusted_runtime_edge_event_type", "DEVICE_RUNTIME_SNAPSHOT");
        for (String field : new String[]{"delivery_door_state", "delivery_door_actuator_health", "clean_door_inferred_state"}) row.put(field, "UNKNOWN");
        row.put("delivery_door_contact_state", "UNAVAILABLE");
        row.put("last_delivery_door_command", "CLOSE");
        row.put("last_delivery_door_output_status", "COMMAND_DISPATCHED");
        row.put("delivery_door_physical_state_basis", "NOT_OBSERVABLE");
        row.put("clean_lock_power_state", "DEENERGIZED");
        row.put("clean_solenoid_health", "OK");
        row.put("clean_door_state_basis", "NOT_OBSERVABLE");
        row.put("cleaner_physical_close_confirmed", false);
        row.put("weight_sensor_health", "OK");
        row.put("weight_measurement_uid", UUID.randomUUID().toString());
        row.put("weight_measurement_status", "UNSTABLE");
        row.put("weight_value_kind", "TIMEOUT_MEDIAN");
        row.put("weight_value_available", true);
        row.put("reported_weight_grams", 13250);
        row.put("weight_measurement_elapsed_ms", 5000);
        row.put("weight_sample_count", 20);
        row.put("calibration_version", 4);
        row.put("weight_fault_code", null);
        row.put("weight_mcu_boot_id", 101);
        row.put("weight_mcu_event_sequence", 51);
        row.put("infrared_value", "CLEAR");
        row.put("infrared_sensor_health", "OK");
        row.put("fullness_sensor_kind", "ULTRASONIC");
        row.put("fullness_sensor_value", "CLEAR");
        row.put("fullness_sample_basis", "MEASURED_MEDIAN");
        row.put("representative_distance_mm", 720);
        row.put("fullness_valid_sample_count", 5);
        row.put("runtime_fault_bitmap", 0);
        row.put("smoke_state", "NORMAL");
        row.put("smoke_sensor_health", "OK");
        row.put("safety_status", "SAFE");
        Timestamp now = Timestamp.from(Instant.now());
        row.put("created_at", now);
        row.put("updated_at", now);
        return row;
    }
}
