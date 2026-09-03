package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class BusinessReleaseControlPlaneMigrationTest {

    private static final Path V64 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V64__business_runtime_release_control_plane.sql");

    @Test
    void createsAuditedReleaseAndPlanningStateWithoutDispatchState()
            throws IOException {
        String sql = Files.readString(resolve(V64), StandardCharsets.UTF_8)
                .replace("\r\n", "\n");

        assertThat(sql).contains(
                "ALTER TABLE dev_edge_software_release",
                "version_name REGEXP",
                "CREATE TABLE dev_edge_software_release_sequence",
                "CREATE TABLE dev_edge_software_release_control",
                "CREATE TABLE dev_edge_software_release_action",
                "CREATE TABLE dev_edge_software_rollout",
                "CREATE TABLE dev_edge_software_deployment",
                "CREATE TABLE dev_edge_software_rollout_action",
                "'DRAFT', 'VERIFYING', 'VERIFICATION_FAILED'",
                "'AWAITING_APPROVAL', 'READY', 'SUSPENDED', 'RETIRED'",
                "remote_dispatch_enabled_snapshot = 0",
                "source_software_fact_id BIGINT NOT NULL",
                "eligibility_snapshot JSON NOT NULL",
                "verification_operation_uid CHAR(36)",
                "CREATE DEFINER = 'ecobin_trigger_definer'@'%'",
                "trg_dev_edge_release_control_v64_identity",
                "business release identity and artifact are immutable")
                .doesNotContain(
                        "ops_reliable_task",
                        "START_BUSINESS_RUNTIME_UPDATE",
                        "download_url",
                        "presigned_url");
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
