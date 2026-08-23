package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class FactorySealAuthorizationMigrationTest {

    private static final Path V56 = Path.of(
            "src/main/resources/db/p0-migration",
            "V56__factory_seal_authorization.sql");

    @Test
    void bindsOneAuthorizationToOneAcceptanceGenerationAndReliableTask()
            throws Exception {
        String sql = Files.readString(resolve(), StandardCharsets.UTF_8)
                .toUpperCase(Locale.ROOT);

        assertThat(sql).contains(
                "ADD COLUMN ACCEPTANCE_GENERATION BIGINT UNSIGNED",
                "CREATE TABLE DEV_FACTORY_SEAL_AUTHORIZATION",
                "UNIQUE (ASSET_ID, ACCEPTANCE_GENERATION)",
                "UNIQUE (COMMAND_UID)",
                "UNIQUE (RELIABLE_TASK_UID)",
                "UNIQUE (COMPLETION_EVENT_UID)",
                "FOREIGN KEY (RELIABLE_TASK_UID) REFERENCES OPS_RELIABLE_TASK",
                "FOREIGN KEY (ASSET_ID, ACCEPTANCE_EVIDENCE_UID)",
                "REFERENCES DEV_DEVICE_ACCEPTANCE_EVIDENCE",
                "'AUTHORIZE_FACTORY_SEAL'",
                "'START_MCU_FIRMWARE_UPDATE'",
                "AUTHORIZATION_STATUS = 'ACKNOWLEDGED'",
                "AUTHORIZATION_STATUS = 'CANCELLED'",
                "AUTHORIZATION_STATUS = 'SEALED'",
                "COMPLETION_PAYLOAD_SHA256 BINARY(32)",
                "IMAGE_RELEASE_ID IS NOT NULL",
                "AUTHORIZATION_BINDING_SHA256 BINARY(32)",
                "OPERATOR_CONFIRMATION_UID CHAR(36)",
                "CLEANUP_COMPLETED_AT DATETIME(3)",
                "CLEANUP_COMPLETED_AT >= SEALED_AT");
    }

    private static Path resolve() {
        if (Files.isRegularFile(V56)) {
            return V56;
        }
        return Path.of("ecobin-bootstrap").resolve(V56);
    }
}
