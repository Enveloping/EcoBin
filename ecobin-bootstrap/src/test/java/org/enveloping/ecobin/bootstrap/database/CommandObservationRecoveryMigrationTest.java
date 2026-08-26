package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class CommandObservationRecoveryMigrationTest {

    private static final Path V58 = Path.of(
            "src/main/resources/db/p0-migration",
            "V58__clock_recovery_invariants.sql");

    @Test
    void commandFailureIdentityKeepsTimeoutAndLaterRestartFacts()
            throws Exception {
        String sql = Files.readString(resolve(), StandardCharsets.UTF_8)
                .toUpperCase(Locale.ROOT);

        assertThat(sql).contains(
                "ALTER TABLE DEV_DEVICE_COMMAND_EVENT",
                "DROP INDEX UQ_DEV_COMMAND_EVENT_STAGE",
                "ADD COLUMN OBSERVATION_ERROR_IDENTITY VARCHAR(64)",
                "GENERATED ALWAYS AS (COALESCE(ERROR_CODE, '')) STORED",
                "ADD CONSTRAINT UQ_DEV_COMMAND_EVENT_STAGE_ERROR",
                "COMMAND_ID,\n            OBSERVATION_STAGE,\n            OBSERVATION_ERROR_IDENTITY");
    }

    private static Path resolve() {
        if (Files.isRegularFile(V58)) {
            return V58;
        }
        return Path.of("ecobin-bootstrap").resolve(V58);
    }
}
