package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class DeviceEntryUrlEdgeDeliveryMigrationBoundaryTest {

    private static final Path V42 = Path.of(
            "src",
            "main",
            "resources",
            "db",
            "p0-migration",
            "V42__device_entry_url_edge_delivery.sql");

    @Test
    void addsDurableRolloutAndVersionedAcceptanceFactsWithoutReopeningHistory()
            throws IOException {
        String sql = Files.readString(V42, StandardCharsets.UTF_8);

        assertThat(sql).contains(
                "CREATE TABLE dev_device_entry_url_rollout",
                "ADD COLUMN device_entry_url_stored",
                "ADD COLUMN device_entry_url_sha256",
                "evidence_schema_version = 1",
                "evidence_schema_version >= 2",
                "'SYNC_DEVICE_ENTRY_URL'");
        assertThat(sql).doesNotContain(
                "UPDATE dev_device_asset",
                "acceptance_status = 'PENDING'");
    }
}
