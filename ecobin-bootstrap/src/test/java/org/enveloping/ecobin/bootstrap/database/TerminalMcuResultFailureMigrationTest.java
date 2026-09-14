package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TerminalMcuResultFailureMigrationTest {

    private static final Path V83 = Path.of(
            "src", "main", "resources", "db", "p0-migration",
            "V83__terminal_mcu_result_failure.sql");

    @Test
    void v83OnlyExtendsTheTwoExistingCleanFailureConstraints()
            throws Exception {
        String sql = Files.readString(resolve(V83), StandardCharsets.UTF_8)
                .replace("\r\n", "\n");
        int recoveryConstraint = sql.indexOf(
                "ALTER TABLE rec_clean_bag_recovery");
        assertTrue(sql.contains(
                "DROP CHECK ck_rec_clean_operation_state_shape"));
        assertTrue(sql.contains(
                "DROP CHECK ck_rec_clean_bag_recovery_fault"));
        assertTrue(recoveryConstraint > 0);

        String operation = sql.substring(0, recoveryConstraint);
        String recovery = sql.substring(recoveryConstraint);
        for (String reason : new String[]{
                "MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE",
                "MCU_WORK_CANCELLED",
                "MCU_WORK_FAILED"}) {
            assertTrue(operation.contains("'" + reason + "'"));
            assertTrue(recovery.contains("'" + reason + "'"));
        }
        assertFalse(operation.contains("'MCU_INITIAL_WEIGHT_UNAVAILABLE'"));
        assertFalse(recovery.contains("'MCU_INITIAL_WEIGHT_UNAVAILABLE'"));
        assertTrue(operation.contains("status = 'PRE_UNLOCK_ENDED'"));
        assertTrue(operation.contains("status = 'ABORTED'"));
        assertTrue(operation.contains("status = 'COMPLETED'"));
        String normalizedOperation = operation.replaceAll("\\s+", " ");
        assertTrue(normalizedOperation.contains(
                "edge_saved_confirmed = 1 "
                + "AND first_unlock_may_have_executed = 1 "
                + "AND end_reason IN ( "
                + "'MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE', "
                + "'MCU_WORK_CANCELLED', 'MCU_WORK_FAILED' )"));
    }

    private static Path resolve(Path moduleRelative) {
        Path working = Path.of("").toAbsolutePath();
        Path direct = working.resolve(moduleRelative);
        if (Files.isRegularFile(direct)) {
            return direct;
        }
        return working.resolve("ecobin-bootstrap").resolve(moduleRelative);
    }
}
