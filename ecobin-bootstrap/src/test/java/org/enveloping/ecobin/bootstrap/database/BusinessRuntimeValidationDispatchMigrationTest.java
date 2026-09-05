package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class BusinessRuntimeValidationDispatchMigrationTest {

    private static final Path V65 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V65__business_runtime_validation_dispatch.sql");

    @Test
    void addsSingleDeviceValidationAndImmutableProgressFacts()
            throws IOException {
        String sql = Files.readString(resolve(V65), StandardCharsets.UTF_8)
                .replace("\r\n", "\n");

        assertThat(sql).contains(
                "'DRAFT', 'VALIDATING', 'AWAITING_PROMOTION'",
                "'VALIDATION_FAILED', 'ACTIVE', 'COMPLETED', 'STOPPED'",
                "MODIFY COLUMN rollout_status VARCHAR(24)",
                "MODIFY COLUMN deployment_status VARCHAR(40)",
                "MODIFY COLUMN resulting_status VARCHAR(24)",
                "action_type IN ('CREATE', 'START_VALIDATION', 'STOP')",
                "ADD COLUMN command_uid CHAR(36)",
                "ADD COLUMN reliable_task_uid CHAR(36)",
                "ADD COLUMN edge_update_uid CHAR(36)",
                "ADD COLUMN control_sequence BIGINT UNSIGNED",
                "CREATE TABLE dev_edge_software_deployment_progress",
                "CONSTRAINT uq_dev_edge_progress_event UNIQUE (event_uid)",
                "CONSTRAINT uq_dev_edge_progress_inbox UNIQUE (source_inbox_id)",
                "deployment_id, stage_sequence",
                "trg_dev_edge_progress_v65_immutable",
                "trg_dev_edge_progress_v65_no_delete",
                "business deployment progress is immutable")
                .doesNotContain(
                        "download_url",
                        "presigned_url",
                        "cos_grant");
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
