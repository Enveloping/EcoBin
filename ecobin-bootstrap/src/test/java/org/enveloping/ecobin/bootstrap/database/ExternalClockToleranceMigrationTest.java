package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class ExternalClockToleranceMigrationTest {

    private static final Path V57 = Path.of(
            "src/main/resources/db/p0-migration",
            "V57__external_clock_tolerance_and_authorization_recovery.sql");
    private static final Path V58 = Path.of(
            "src/main/resources/db/p0-migration",
            "V58__clock_recovery_invariants.sql");

    @Test
    void externalTimesRemainEvidenceInsteadOfDatabaseOrderingAuthorities()
            throws Exception {
        String sql = Files.readString(resolve(), StandardCharsets.UTF_8)
                .toUpperCase(Locale.ROOT);

        assertThat(sql).contains(
                "ADD COLUMN PACKAGE_EXPIRES_AT DATETIME(3)",
                "DATE_ADD(UPDATED_AT, INTERVAL 10 MINUTE)",
                "LEAST(",
                "ADD COLUMN CLOCK_QUALITY VARCHAR(16)",
                "MODIFY COLUMN OBSERVED_AT DATETIME(3) NULL",
                "DROP CHECK CK_DEV_ACCEPTANCE_EVIDENCE_RESULT_V42",
                "EVIDENCE_SCHEMA_VERSION = 1",
                "OR DEVICE_ENTRY_URL_STORED = 1",
                "MODIFY COLUMN OCCURRED_AT DATETIME(3) NULL",
                "MODIFY COLUMN DEVICE_OCCURRED_AT DATETIME(3) NULL",
                "SESSION_ID, RECEIVED_AT DESC, ID DESC",
                "ADD COLUMN COMPLETION_CLOCK_QUALITY VARCHAR(16)",
                "COMPLETION_CLOCK_QUALITY IN ('ESTIMATED', 'UNAVAILABLE')",
                "SEALED_AT IS NULL",
                "CLEANUP_COMPLETED_AT IS NULL");
        assertThat(sql).doesNotContain(
                "CHANNEL_CREATED_AT >= CREATED_AT",
                "CHANNEL_UPDATED_AT >= CREATED_AT",
                "RECEIVED_AT >= OBSERVED_AT",
                "RECEIVED_AT >= OCCURRED_AT",
                "BACKEND_RECEIVED_AT >= DEVICE_OCCURRED_AT",
                "CLEANUP_COMPLETED_AT >= SEALED_AT",
                "AND MCU_SIMULATED = 0",
                "AND CAMERAS_SIMULATED = 0");
    }

    @Test
    void recoveryConstraintsRejectNullSealQualityAndKeepWechatTimeDiagnostic()
            throws Exception {
        String sql = Files.readString(resolve(V58), StandardCharsets.UTF_8)
                .toUpperCase(Locale.ROOT);

        assertThat(sql).contains(
                "DROP INDEX UQ_DEV_COMMAND_EVENT_STAGE",
                "GENERATED ALWAYS AS (COALESCE(ERROR_CODE, '')) STORED",
                "ADD CONSTRAINT UQ_DEV_COMMAND_EVENT_STAGE_ERROR",
                "COMMAND_ID,\n            OBSERVATION_STAGE,\n            OBSERVATION_ERROR_IDENTITY",
                "COMPLETION_CLOCK_QUALITY IS NOT NULL",
                "SET COMPLETION_CLOCK_QUALITY = 'SYNCED'",
                "PACKAGE_EXPIRES_AT > UTC_TIMESTAMP(3)",
                "LOCAL_STATE = 'WAIT_USER_CONFIRM'",
                "LOCAL_STATE = 'ACTIVE'",
                "LOCAL_STATE = 'CLOSED'",
                "CHANNEL_CREATED_AT IS NULL",
                "COALESCE(SUBMITTED_AT, CREATED_AT)",
                "CONFIRMATION_DEADLINE_AT = DATE_ADD(\n                CREATED_AT,",
                "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING",
                "REASON=WAITING_DISPLAY_PACKAGE_UNRECOVERABLE;",
                "OBSERVATION_ROW.OBSERVED_CHANNEL_CREATED_AT =\n            AUTHORIZATION_ROW.CHANNEL_CREATED_AT",
                "RECOVERY_ISSUE.STATE = 'RESOLVED'",
                "AUTHORIZATION_ROW.LOCAL_STATE IN (");
        assertThat(sql).doesNotContain(
                "AUTHORIZED_AT >= CHANNEL_CREATED_AT",
                "CLOSED_AT >= CHANNEL_CREATED_AT");
        assertThat(sql).containsSubsequence(
                "LOCAL_STATE = 'WAIT_USER_CONFIRM'",
                "CHANNEL_STATE IS NOT NULL",
                "CHANNEL_STATE = 'WAIT_USER_CONFIRM'");
        assertThat(sql).containsSubsequence(
                "LOCAL_STATE = 'EXPIRED'",
                "CHANNEL_STATE IS NOT NULL",
                "LAST_API_ERROR_CODE IS NOT NULL",
                "CLOSE_REASON IS NOT NULL");
    }

    private static Path resolve() {
        return resolve(V57);
    }

    private static Path resolve(Path migration) {
        if (Files.isRegularFile(migration)) {
            return migration;
        }
        return Path.of("ecobin-bootstrap").resolve(migration);
    }
}
