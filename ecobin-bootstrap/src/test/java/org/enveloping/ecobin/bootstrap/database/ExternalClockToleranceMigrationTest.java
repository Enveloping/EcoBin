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
                "CLEANUP_COMPLETED_AT >= SEALED_AT");
    }

    private static Path resolve() {
        if (Files.isRegularFile(V57)) {
            return V57;
        }
        return Path.of("ecobin-bootstrap").resolve(V57);
    }
}
