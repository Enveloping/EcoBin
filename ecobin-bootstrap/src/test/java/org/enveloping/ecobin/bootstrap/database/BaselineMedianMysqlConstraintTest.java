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
@EnabledIfEnvironmentVariable(named = "ECOBIN_BASELINE_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_baseline_p1av(?:\\?.*)?")
class BaselineMedianMysqlConstraintTest {
    private Connection connection;

    @BeforeEach
    void open() throws Exception {
        connection = DriverManager.getConnection(System.getenv("ECOBIN_BASELINE_MEDIAN_MYSQL_URL"), "root", "");
        assertEquals("ecobin_baseline_p1av", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        try (var statement = connection.createStatement()) {
            statement.execute("CREATE TEMPORARY TABLE median_baseline LIKE dev_physical_result");
        }
    }

    @AfterEach
    void close() throws Exception { if (connection != null) connection.close(); }

    @Test
    void storesTimeoutMedianAsPrimaryWeightWithoutRelabelingQuality() throws Exception {
        assertEquals(1, insert(medianBaseline()));
        try (var statement = connection.createStatement(); var result = statement.executeQuery(
                "SELECT baseline_measurement_status,baseline_weight_value_kind,baseline_total_weight_g,"
                        + "baseline_last_observed_weight_g,baseline_fault_code FROM median_baseline")) {
            assertTrue(result.next());
            assertEquals("UNSTABLE", result.getString(1));
            assertEquals("TIMEOUT_MEDIAN", result.getString(2));
            assertEquals(1180, result.getLong(3));
            assertNull(result.getObject(4));
            assertNull(result.getObject(5));
        }
    }

    @ParameterizedTest
    @MethodSource("invalidMedian")
    void rejectsInvalidMedianWithoutSqlNullBypass(String field, Object value) {
        Map<String, Object> row = medianBaseline();
        row.put(field, value);
        assertThrows(SQLException.class, () -> insert(row));
    }

    static Stream<Arguments> invalidMedian() {
        return Stream.of(
                Arguments.of("baseline_measurement_status", "STABLE"), Arguments.of("baseline_measurement_status", null),
                Arguments.of("baseline_weight_value_available", false), Arguments.of("baseline_weight_value_available", null),
                Arguments.of("baseline_weight_value_kind", null), Arguments.of("baseline_weight_value_kind", "UNKNOWN_KIND"),
                Arguments.of("baseline_total_weight_g", null), Arguments.of("baseline_total_weight_g", 2147483648L),
                Arguments.of("baseline_total_weight_g", -2147483649L), Arguments.of("baseline_last_observed_weight_g", 1180),
                Arguments.of("baseline_measurement_elapsed_ms", 4999), Arguments.of("baseline_measurement_elapsed_ms", 5001),
                Arguments.of("baseline_measurement_elapsed_ms", null), Arguments.of("baseline_sample_count", 4),
                Arguments.of("baseline_sample_count", 33), Arguments.of("baseline_sample_count", null),
                Arguments.of("baseline_sensor_health", "TIMEOUT"), Arguments.of("baseline_sensor_health", null),
                Arguments.of("baseline_fault_code", "WEIGHT_UNSTABLE"), Arguments.of("baseline_fault_code", ""),
                Arguments.of("baseline_calibration_version", -1), Arguments.of("baseline_calibration_version", 4294967296L),
                Arguments.of("baseline_calibration_version", null), Arguments.of("baseline_mcu_boot_id", 0),
                Arguments.of("baseline_mcu_boot_id", 9007199254740992L), Arguments.of("baseline_mcu_boot_id", null),
                Arguments.of("baseline_mcu_event_sequence", 0), Arguments.of("baseline_mcu_event_sequence", 4294967296L),
                Arguments.of("baseline_mcu_event_sequence", null), Arguments.of("baseline_measurement_uid", null),
                Arguments.of("baseline_measurement_uid", "not-a-uuid"),
                Arguments.of("baseline_measurement_uid", "86000000-0000-1000-8000-000000000002"),
                Arguments.of("baseline_measurement_id", null), Arguments.of("empty_bag_confirmed", false),
                Arguments.of("empty_bag_confirmed", null));
    }

    @ParameterizedTest
    @MethodSource("validBounds")
    void preservesExactPhysicalValueBoundsEvenWhenNegativeCannotEstablishTare(long grams, int count, long calibration, long boot, long sequence) throws Exception {
        Map<String, Object> row = medianBaseline();
        row.put("baseline_total_weight_g", grams);
        row.put("baseline_sample_count", count);
        row.put("baseline_calibration_version", calibration);
        row.put("baseline_mcu_boot_id", boot);
        row.put("baseline_mcu_event_sequence", sequence);
        assertEquals(1, insert(row));
    }

    static Stream<Arguments> validBounds() {
        return Stream.of(Arguments.of(-2147483648L, 5, 0L, 1L, 1L),
                Arguments.of(2147483647L, 32, 4294967295L, 9007199254740991L, 4294967295L));
    }

    @Test
    void historicalStableResultCanKeepUnknownValueMetadata() throws Exception {
        Map<String, Object> row = medianBaseline();
        row.put("baseline_measurement_status", "STABLE");
        row.put("baseline_weight_value_kind", null);
        row.put("baseline_weight_value_available", null);
        row.put("baseline_sample_count", 1);
        row.put("baseline_measurement_elapsed_ms", 0);
        assertEquals(1, insert(row));
    }

    @Test
    void legacyUnstableDiagnosticMeanDoesNotBecomePrimaryWeight() throws Exception {
        Map<String, Object> row = medianBaseline();
        row.put("baseline_weight_value_kind", "LAST_FOUR_MEAN");
        row.put("baseline_total_weight_g", null);
        row.put("baseline_last_observed_weight_g", 1180);
        row.put("baseline_fault_code", "WEIGHT_UNSTABLE");
        assertEquals(1, insert(row));
    }

    @Test
    void genuineUnavailableTimeoutKeepsNullWeightAndFailure() throws Exception {
        Map<String, Object> row = medianBaseline();
        row.put("baseline_measurement_status", "TIMEOUT");
        row.put("baseline_weight_value_kind", "NONE");
        row.put("baseline_weight_value_available", false);
        row.put("baseline_total_weight_g", null);
        row.put("baseline_sample_count", 0);
        row.put("baseline_sensor_health", "TIMEOUT");
        row.put("baseline_fault_code", "WEIGHT_TIMEOUT");
        assertEquals(1, insert(row));
    }

    private int insert(Map<String, Object> row) throws SQLException {
        String sql = "INSERT INTO median_baseline (" + String.join(",", row.keySet()) + ") VALUES ("
                + String.join(",", Collections.nCopies(row.size(), "?")) + ")";
        try (var statement = connection.prepareStatement(sql)) {
            int index = 1;
            for (Object value : row.values()) statement.setObject(index++, value);
            return statement.executeUpdate();
        }
    }

    private static Map<String, Object> medianBaseline() {
        Map<String, Object> row = new LinkedHashMap<>();
        for (String field : new String[]{"tenant_id", "organization_id", "asset_id", "port_id", "edge_event_id",
                "command_id", "baseline_measurement_id"}) row.put(field, 1L);
        row.put("edge_event_type", "BASELINE_MEASUREMENT_COMPLETE");
        row.put("command_type", "MEASURE_EMPTY_BAG_BASELINE");
        row.put("reported_config_version_no", 8L);
        row.put("reported_config_content_sha256", new byte[32]);
        row.put("reported_config_mcu_payload_sha256", new byte[32]);
        row.put("result_type", "BASELINE_MEASUREMENT");
        row.put("baseline_measurement_uid", UUID.randomUUID().toString());
        row.put("baseline_measurement_status", "UNSTABLE");
        row.put("baseline_weight_value_kind", "TIMEOUT_MEDIAN");
        row.put("baseline_weight_value_available", true);
        row.put("baseline_total_weight_g", 1180);
        row.put("baseline_last_observed_weight_g", null);
        row.put("baseline_measurement_elapsed_ms", 5000);
        row.put("baseline_sample_count", 20);
        row.put("baseline_calibration_version", 4);
        row.put("baseline_sensor_health", "OK");
        row.put("baseline_fault_code", null);
        row.put("baseline_mcu_boot_id", 101);
        row.put("baseline_mcu_event_sequence", 47);
        row.put("empty_bag_confirmed", true);
        row.put("created_at", Timestamp.from(Instant.now()));
        return row;
    }
}
