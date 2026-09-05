package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class BusinessRuntimeCancellationMigrationTest {

    private static final Path V66 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V66__business_runtime_update_cancellation.sql");

    @Test
    void separatesCancellationRequestFromImmutableDeviceResult()
            throws IOException {
        String sql = Files.readString(resolve(V66), StandardCharsets.UTF_8)
                .replace("\r\n", "\n");

        assertThat(sql).contains(
                "DROP CHECK ck_ops_task_sources_v56",
                "ADD CONSTRAINT ck_ops_task_sources_v66",
                "'START_BUSINESS_RUNTIME_UPDATE'",
                "'CANCEL_BUSINESS_RUNTIME_UPDATE'",
                "ADD COLUMN cancel_command_uid CHAR(36)",
                "ADD COLUMN cancel_reliable_task_uid CHAR(36)",
                "ADD COLUMN cancel_control_sequence BIGINT UNSIGNED",
                "ADD COLUMN cancellation_status VARCHAR(16)",
                "'NONE', 'QUEUED', 'CANCELLED', 'TOO_LATE'",
                "action_type IN ('CREATE', 'START_VALIDATION', 'REQUEST_CANCEL', 'STOP')",
                "CREATE TABLE dev_edge_software_deployment_cancel_result",
                "CONSTRAINT uq_dev_edge_cancel_result_event UNIQUE (event_uid)",
                "CONSTRAINT uq_dev_edge_cancel_result_inbox UNIQUE (source_inbox_id)",
                "CONSTRAINT uq_dev_edge_cancel_result_command UNIQUE (cancel_command_uid)",
                "(result = 'CANCELLED' AND error_code IS NULL)",
                "error_code = 'BUSINESS_UPDATE_CANCEL_TOO_LATE'",
                "trg_dev_edge_cancel_result_v66_immutable",
                "trg_dev_edge_cancel_result_v66_no_delete")
                .doesNotContain(
                        "download_url",
                        "presigned_url",
                        "secret_key",
                        "session_token");
    }

    private static Path resolve(Path relative) {
        Path direct = relative.toAbsolutePath().normalize();
        if (Files.exists(direct)) {
            return direct;
        }
        return Path.of("ecobin-bootstrap")
                .resolve(relative)
                .toAbsolutePath()
                .normalize();
    }
}
