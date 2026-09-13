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

/** Migrated MySQL CHECKs and unique keys; temporary LIKE tables omit foreign keys/triggers. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_FULLNESS_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_fullness_p1aw(?:\\?.*)?")
class FullnessMedianMysqlConstraintTest {
    private Connection connection;

    @BeforeEach
    void open() throws Exception {
        connection = DriverManager.getConnection(System.getenv("ECOBIN_FULLNESS_MEDIAN_MYSQL_URL"), "root", "");
        assertEquals("ecobin_fullness_p1aw", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        try (var statement = connection.createStatement()) {
            statement.execute("CREATE TEMPORARY TABLE median_fullness LIKE dev_fullness_state_fact");
        }
    }

    @AfterEach
    void close() throws Exception { if (connection != null) connection.close(); }

    @Test
    void storesTimeoutMedianAsPrimaryWeightWithoutRelabelingQuality() throws Exception {
        assertEquals(1, insert(medianFullness()));
        try (var statement = connection.createStatement(); var result = statement.executeQuery(
                "SELECT measurement_status,weight_value_kind,total_weight_g,fault_code FROM median_fullness")) {
            assertTrue(result.next());
            assertEquals("UNSTABLE", result.getString(1));
            assertEquals("TIMEOUT_MEDIAN", result.getString(2));
            assertEquals(51200, result.getLong(3));
            assertNull(result.getObject(4));
        }
    }

    @ParameterizedTest
    @MethodSource("invalidMedian")
    void rejectsInvalidMedianWithoutSqlNullBypass(String field, Object value) {
        Map<String, Object> row = medianFullness();
        row.put(field, value);
        assertThrows(SQLException.class, () -> insert(row));
    }

    static Stream<Arguments> invalidMedian() {
        return Stream.of(
                Arguments.of("measurement_status", "STABLE"), Arguments.of("measurement_status", null),
                Arguments.of("weight_value_available", false), Arguments.of("weight_value_available", null),
                Arguments.of("weight_value_kind", null), Arguments.of("weight_value_kind", "UNKNOWN_KIND"),
                Arguments.of("total_weight_g", null), Arguments.of("total_weight_g", 2147483648L),
                Arguments.of("total_weight_g", -2147483649L),
                Arguments.of("measurement_elapsed_ms", 4999), Arguments.of("measurement_elapsed_ms", 5001),
                Arguments.of("measurement_elapsed_ms", null), Arguments.of("sample_count", 4),
                Arguments.of("sample_count", 33), Arguments.of("sample_count", null),
                Arguments.of("sensor_health", "TIMEOUT"), Arguments.of("sensor_health", null),
                Arguments.of("fault_code", "WEIGHT_UNSTABLE"), Arguments.of("fault_code", ""),
                Arguments.of("calibration_version", -1), Arguments.of("calibration_version", 4294967296L),
                Arguments.of("calibration_version", null), Arguments.of("mcu_boot_id", 0),
                Arguments.of("mcu_boot_id", 9007199254740992L), Arguments.of("mcu_boot_id", null),
                Arguments.of("mcu_event_sequence", 0), Arguments.of("mcu_event_sequence", 4294967296L),
                Arguments.of("mcu_event_sequence", null), Arguments.of("measurement_uid", null),
                Arguments.of("measurement_uid", "not-a-uuid"),
                Arguments.of("measurement_uid", "86000000-0000-1000-8000-000000000002"));
    }

    @ParameterizedTest
    @MethodSource("validBounds")
    void preservesExactPhysicalValueBounds(long grams, int count, long calibration, long boot, long sequence) throws Exception {
        Map<String, Object> row = medianFullness();
        row.put("total_weight_g", grams);
        row.put("sample_count", count);
        row.put("calibration_version", calibration);
        row.put("mcu_boot_id", boot);
        row.put("mcu_event_sequence", sequence);
        assertEquals(1, insert(row));
    }

    static Stream<Arguments> validBounds() {
        return Stream.of(Arguments.of(-2147483648L, 5, 0L, 1L, 1L),
                Arguments.of(2147483647L, 32, 4294967295L, 9007199254740991L, 4294967295L));
    }

    @Test
    void historicalStableResultCanKeepUnknownValueMetadata() throws Exception {
        Map<String, Object> row = medianFullness();
        row.put("measurement_status", "STABLE");
        row.put("weight_value_kind", null);
        row.put("weight_value_available", null);
        row.put("sample_count", 1);
        row.put("measurement_elapsed_ms", 0);
        assertEquals(1, insert(row));
    }

    private int insert(Map<String, Object> row) throws SQLException {
        String sql = "INSERT INTO median_fullness (" + String.join(",", row.keySet()) + ") VALUES ("
                + String.join(",", Collections.nCopies(row.size(), "?")) + ")";
        try (var statement = connection.prepareStatement(sql)) {
            int index = 1;
            for (Object value : row.values()) statement.setObject(index++, value);
            return statement.executeUpdate();
        }
    }

    private static Map<String, Object> medianFullness() {
        Map<String, Object> row = new LinkedHashMap<>();
        for (String field : new String[]{"tenant_id", "organization_id", "asset_id", "port_id", "edge_event_id"})
            row.put(field, 1L);
        for (String field : new String[]{"state_change_uid", "bag_uid", "source_work_uid", "measurement_uid"})
            row.put(field, UUID.randomUUID().toString());
        row.put("edge_event_sequence", 1045);
        row.put("reported_state", "FULL");
        row.put("source_work_type", "CLEAN_OPERATION");
        row.put("fullness_mode", "WEIGHT_ONLY");
        row.put("fullness_sensor_kind", "DIGITAL_INFRARED");
        row.put("fullness_sensor_value", "CLEAR");
        row.put("confirmation_basis", "MCU_INDEPENDENT_RECHECK");
        row.put("reported_config_version_no", 8L);
        row.put("reported_config_content_sha256", new byte[32]);
        row.put("reported_config_mcu_payload_sha256", new byte[32]);
        row.put("measurement_status", "UNSTABLE");
        row.put("weight_value_kind", "TIMEOUT_MEDIAN");
        row.put("weight_value_available", true);
        row.put("total_weight_g", 51200);
        row.put("measurement_elapsed_ms", 5000);
        row.put("sample_count", 20);
        row.put("calibration_version", 4);
        row.put("sensor_health", "OK");
        row.put("fault_code", null);
        row.put("mcu_boot_id", 101);
        row.put("mcu_event_sequence", 47);
        row.put("configured_full_weight_g", 50000);
        row.put("baseline_weight_g", 1200);
        row.put("fullness_percent_hundredths", 10000);
        row.put("weight_full", true);
        row.put("backend_received_at", Timestamp.from(Instant.now()));
        row.put("created_at", Timestamp.from(Instant.now()));
        return row;
    }
}
