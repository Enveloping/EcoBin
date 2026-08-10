package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class RuntimeSnapshotPolicyMigrationBoundaryTest {

    private static final Path V46 = Path.of(
            "src", "main", "resources", "db", "p0-migration",
            "V46__global_runtime_snapshot_policy.sql");

    @Test
    void addsOneGlobalPolicyAndTagsNewImmutableConfigurations()
            throws IOException {
        String sql = Files.readString(V46, StandardCharsets.UTF_8);

        assertThat(sql).contains(
                "CREATE TABLE dev_runtime_snapshot_policy",
                "singleton_id = 1",
                "fallback_interval_ms BETWEEN 600000 AND 4294967295",
                "1, 1, 3600000",
                "ADD COLUMN runtime_snapshot_policy_version_no BIGINT NULL",
                "ix_dev_config_runtime_snapshot_policy");
        assertThat(sql).doesNotContain(
                "DELETE FROM",
                "DROP TABLE",
                "TRUNCATE TABLE",
                "CREATE TABLE dev_runtime_snapshot_archive");
    }
}
