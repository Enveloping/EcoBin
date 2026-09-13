package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
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

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Exercises the constraints installed by V81 in a disposable MySQL 8.4
 * database. Temporary LIKE tables retain the migrated CHECK constraints but
 * intentionally omit foreign keys; the full V1..V81 run verifies the foreign
 * keys can be installed against their real parent tables.
 */
@EnabledIfEnvironmentVariable(
        named = "ECOBIN_ISSUE_RELIABLE_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_issue_p1bl")
class InterruptedCleanBagRecoveryMysqlConstraintTest {

    private Connection connection;

    @BeforeEach
    void open() throws Exception {
        connection = DriverManager.getConnection(
                System.getenv("ECOBIN_ISSUE_RELIABLE_MYSQL_URL"),
                "root",
                "");
        assertEquals("ecobin_issue_p1bl", connection.getCatalog());
        assertTrue(connection.getMetaData()
                .getDatabaseProductVersion().startsWith("8.4."));
        try (var statement = connection.createStatement()) {
            statement.execute("""
                    CREATE TEMPORARY TABLE clean_bag_recovery_shape
                    LIKE rec_clean_bag_recovery
                    """);
        }
    }

    @AfterEach
    void close() throws Exception {
        if (connection != null) {
            connection.close();
        }
    }

    @ParameterizedTest
    @MethodSource("validRecoveryStates")
    void allowsOnlyTheTwoConfirmedBagRecoveryStateMachines(
            String decision,
            String status,
            boolean completed) throws Exception {
        Map<String, Object> row = recovery(decision, status, completed);
        assertEquals(1, insertRecovery(row));
    }

    private static Stream<Object[]> validRecoveryStates() {
        return Stream.of(
                new Object[]{"RETAIN_OLD_BAG", "COMPLETED", true},
                new Object[]{"USE_RESERVED_NEW_BAG", "BASELINE_PENDING", false},
                new Object[]{"USE_RESERVED_NEW_BAG", "BASELINE_REQUIRED", false},
                new Object[]{"USE_RESERVED_NEW_BAG", "COMPLETED", true});
    }

    @ParameterizedTest
    @MethodSource("invalidRecoveryStates")
    void rejectsUnknownBagChoiceAndContradictoryTerminalState(
            String decision,
            String status,
            boolean completed) {
        Map<String, Object> row = recovery(decision, status, completed);
        assertCheckRejected(() -> insertRecovery(row));
    }

    private static Stream<Object[]> invalidRecoveryStates() {
        return Stream.of(
                new Object[]{"USE_THIRD_BAG", "COMPLETED", true},
                new Object[]{"RETAIN_OLD_BAG", "BASELINE_PENDING", false},
                new Object[]{"RETAIN_OLD_BAG", "COMPLETED", false},
                new Object[]{"USE_RESERVED_NEW_BAG", "BASELINE_PENDING", true},
                new Object[]{"USE_RESERVED_NEW_BAG", "COMPLETED", false},
                new Object[]{"USE_RESERVED_NEW_BAG", "ABORTED", true});
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "WEIGHT_TIMEOUT",
            "MCU_RESTART_WEIGHT_ALL_LOST"
    })
    void rejectsFaultsOutsideTheTwoConfirmedManualRecoverySources(
            String faultCode) {
        Map<String, Object> row = recovery(
                "RETAIN_OLD_BAG", "COMPLETED", true);
        row.put("source_fault_code", faultCode);
        assertCheckRejected(() -> insertRecovery(row));
    }

    @Test
    void allowsLegacyEdgeRestartOnlyAsARecordedRecoverySource()
            throws Exception {
        Map<String, Object> row = recovery(
                "RETAIN_OLD_BAG", "COMPLETED", true);
        row.put("source_fault_code", "EDGE_RESTARTED");

        assertEquals(1, insertRecovery(row));
    }

    @Test
    void rejectsBlankReasonBackwardsCompletionAndNegativeVersion() {
        Map<String, Object> blank = recovery(
                "RETAIN_OLD_BAG", "COMPLETED", true);
        blank.put("operator_reason", "   ");
        assertCheckRejected(() -> insertRecovery(blank));

        Map<String, Object> backwards = recovery(
                "RETAIN_OLD_BAG", "COMPLETED", true);
        backwards.put("completed_at", Timestamp.from(
                Instant.parse("2026-09-13T09:59:59Z")));
        assertCheckRejected(() -> insertRecovery(backwards));

        Map<String, Object> negative = recovery(
                "RETAIN_OLD_BAG", "COMPLETED", true);
        negative.put("lock_version", -1L);
        assertCheckRejected(() -> insertRecovery(negative));
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "MCU_RESTART_FINAL_RESULT_UNAVAILABLE",
            "MCU_COMMUNICATION_UNAVAILABLE"
    })
    void cleanOperationAllowsTheTwoRecoveryFaultsAsAbortedTerminalFacts(
            String reason) throws Exception {
        createCleanTerminalShape();
        assertEquals(1, insertCleanTerminal(
                "ABORTED", true, true, false, false,
                null, timestamp("2026-09-13T10:00:00Z"), reason));
    }

    @ParameterizedTest
    @MethodSource("invalidCleanTerminalFacts")
    void cleanOperationRejectsContradictoryOrInventedAbortFacts(
            String status,
            boolean edgeSaved,
            boolean unlockMayHaveExecuted,
            Long completionRecordId,
            Timestamp endedAt,
            String endReason) throws Exception {
        createCleanTerminalShape();
        assertCheckRejected(() -> insertCleanTerminal(
                status, edgeSaved, unlockMayHaveExecuted, false, false,
                completionRecordId, endedAt, endReason));
    }

    private static Stream<Object[]> invalidCleanTerminalFacts() {
        Timestamp ended = timestamp("2026-09-13T10:00:00Z");
        return Stream.of(
                new Object[]{"ABORTED", true, true, null, ended,
                        "WEIGHT_TIMEOUT"},
                new Object[]{"ABORTED", true, true, null, null,
                        "MCU_COMMUNICATION_UNAVAILABLE"},
                new Object[]{"ABORTED", true, true, 99L, ended,
                        "MCU_COMMUNICATION_UNAVAILABLE"},
                new Object[]{"IN_PROGRESS", true, true, null, ended,
                        "MCU_COMMUNICATION_UNAVAILABLE"});
    }

    @Test
    void recoveryBagOccupancyEventsRequireTheirOriginalCleanOperation()
            throws Exception {
        createBagEventShape();
        assertEquals(1, insertBagEvent(
                "REMOVED_BY_CLEAN_RECOVERY", 91L));
        assertEquals(1, insertBagEvent(
                "INSTALLED_BY_CLEAN_RECOVERY", 91L));
        assertCheckRejected(() -> insertBagEvent(
                "REMOVED_BY_CLEAN_RECOVERY", null));
        assertCheckRejected(() -> insertBagEvent(
                "INITIAL_INSTALLED", 91L));
    }

    private void createCleanTerminalShape() throws Exception {
        String clause = checkClause(
                "rec_clean_operation",
                "ck_rec_clean_operation_state_shape");
        try (var statement = connection.createStatement()) {
            statement.execute("DROP TEMPORARY TABLE IF EXISTS clean_terminal_shape");
            statement.execute("""
                    CREATE TEMPORARY TABLE clean_terminal_shape (
                        status VARCHAR(24) NOT NULL,
                        edge_saved_confirmed TINYINT NOT NULL,
                        first_unlock_may_have_executed TINYINT NOT NULL,
                        clean_lock_deenergized_confirmed TINYINT NOT NULL,
                        cleaner_physical_close_confirmed TINYINT NOT NULL,
                        completion_record_id BIGINT NULL,
                        ended_at DATETIME(3) NULL,
                        end_reason VARCHAR(64) NULL,
                        CONSTRAINT installed_clean_terminal_shape CHECK (
                    """ + clause + "))");
        }
    }

    private void createBagEventShape() throws Exception {
        String clause = checkClause(
                "rec_bag_occupancy_event",
                "ck_rec_bag_event_source");
        try (var statement = connection.createStatement()) {
            statement.execute("""
                    CREATE TEMPORARY TABLE clean_bag_event_shape (
                        event_type VARCHAR(40) NOT NULL,
                        clean_operation_id BIGINT NULL,
                        CONSTRAINT installed_clean_bag_event_shape CHECK (
                    """ + clause + "))");
        }
    }

    private String checkClause(String table, String constraint)
            throws Exception {
        try (var statement = connection.prepareStatement("""
                SELECT cc.check_clause
                FROM information_schema.table_constraints tc
                JOIN information_schema.check_constraints cc
                  ON cc.constraint_schema = tc.constraint_schema
                 AND cc.constraint_name = tc.constraint_name
                WHERE tc.table_schema = DATABASE()
                  AND tc.table_name = ?
                  AND tc.constraint_name = ?
                  AND tc.constraint_type = 'CHECK'
                """)) {
            statement.setString(1, table);
            statement.setString(2, constraint);
            try (var result = statement.executeQuery()) {
                assertTrue(result.next(), "installed CHECK is missing");
                String clause = result.getString(1);
                assertNotNull(clause);
                // information_schema renders string literals as \'value\'.
                // That is display escaping, not reusable CREATE TABLE SQL.
                return clause.replace("\\'", "'");
            }
        }
    }

    private int insertRecovery(Map<String, Object> row)
            throws SQLException {
        String sql = "INSERT INTO clean_bag_recovery_shape ("
                + String.join(",", row.keySet()) + ") VALUES ("
                + String.join(",", Collections.nCopies(row.size(), "?"))
                + ")";
        try (var statement = connection.prepareStatement(sql)) {
            int index = 1;
            for (Object value : row.values()) {
                statement.setObject(index++, value);
            }
            return statement.executeUpdate();
        }
    }

    private int insertCleanTerminal(
            String status,
            boolean edgeSaved,
            boolean unlockMayHaveExecuted,
            boolean lockDeenergized,
            boolean physicalClose,
            Long completionRecordId,
            Timestamp endedAt,
            String endReason) throws SQLException {
        try (var statement = connection.prepareStatement("""
                INSERT INTO clean_terminal_shape (
                    status, edge_saved_confirmed,
                    first_unlock_may_have_executed,
                    clean_lock_deenergized_confirmed,
                    cleaner_physical_close_confirmed,
                    completion_record_id, ended_at, end_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """)) {
            statement.setString(1, status);
            statement.setBoolean(2, edgeSaved);
            statement.setBoolean(3, unlockMayHaveExecuted);
            statement.setBoolean(4, lockDeenergized);
            statement.setBoolean(5, physicalClose);
            statement.setObject(6, completionRecordId);
            statement.setTimestamp(7, endedAt);
            statement.setString(8, endReason);
            return statement.executeUpdate();
        }
    }

    private int insertBagEvent(String eventType, Long cleanOperationId)
            throws SQLException {
        try (var statement = connection.prepareStatement("""
                INSERT INTO clean_bag_event_shape (
                    event_type, clean_operation_id
                ) VALUES (?, ?)
                """)) {
            statement.setString(1, eventType);
            statement.setObject(2, cleanOperationId);
            return statement.executeUpdate();
        }
    }

    private static Map<String, Object> recovery(
            String decision,
            String status,
            boolean completed) {
        Timestamp created = timestamp("2026-09-13T09:59:58Z");
        Timestamp requested = timestamp("2026-09-13T10:00:00Z");
        Timestamp updated = timestamp("2026-09-13T10:00:02Z");
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("recovery_uid", UUID.randomUUID().toString());
        row.put("tenant_id", 1L);
        row.put("organization_id", 2L);
        row.put("asset_id", 3L);
        row.put("port_id", 4L);
        row.put("clean_operation_id", 5L);
        row.put("actual_bag_id", 6L);
        row.put("decision", decision);
        row.put("status", status);
        row.put("source_fault_code",
                "MCU_RESTART_FINAL_RESULT_UNAVAILABLE");
        row.put("cleaner_organization_user_id", 7L);
        row.put("operator_reason", "现场已核对实际袋");
        row.put("requested_at", requested);
        row.put("completed_at", completed ? updated : null);
        row.put("lock_version", 0L);
        row.put("created_at", created);
        row.put("updated_at", updated);
        return row;
    }

    private static Timestamp timestamp(String value) {
        return Timestamp.from(Instant.parse(value));
    }

    private static void assertCheckRejected(SqlAction action) {
        SQLException failure = assertThrows(SQLException.class, action::run);
        assertEquals(3819, failure.getErrorCode(),
                "must fail a MySQL CHECK constraint");
    }

    @FunctionalInterface
    private interface SqlAction {
        int run() throws SQLException;
    }
}
