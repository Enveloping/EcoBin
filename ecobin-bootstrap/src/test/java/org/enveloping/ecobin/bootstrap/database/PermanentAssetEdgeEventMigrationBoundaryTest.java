package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class PermanentAssetEdgeEventMigrationBoundaryTest {

    private static final Path V40 = Path.of(
            "src",
            "main",
            "resources",
            "db",
            "p0-migration",
            "V40__permanent_asset_edge_event_targets.sql");

    @Test
    void edgeEventsUseOnlyPermanentAssetTargets() throws IOException {
        String sql = Files.readString(V40, StandardCharsets.UTF_8);

        assertThat(sql)
                .contains(
                        "DROP CHECK ck_dev_edge_event_target_pair",
                        "DROP CHECK ck_dev_edge_event_target_type",
                        "SET target_type = 'DEVICE_ASSET'",
                        "WHERE target_type = 'DEVICE_DEPLOYMENT'",
                        "AND target_type = 'DEVICE_ASSET'")
                .doesNotContain("AND target_type = 'DEVICE_DEPLOYMENT'");
    }
}
