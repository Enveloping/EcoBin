package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class CleanDeviceListMigrationBoundaryTest {

    private static final Path V45 = Path.of(
            "src", "main", "resources", "db", "p0-migration",
            "V45__clean_device_lists_and_terminal_event_quarantine.sql");

    @Test
    void addsActivityIndexesAndOneTerminalQuarantineReason()
            throws IOException {
        String sql = Files.readString(V45, StandardCharsets.UTF_8);

        assertThat(sql).contains(
                "EVENT_TARGET_NOT_AUTHORITATIVE",
                "ix_rec_delivery_asset_received_v45",
                "backend_received_at DESC",
                "ix_rec_clean_asset_completed_v45",
                "completed_at DESC",
                "ix_rec_fullness_active_confirmed_v45",
                "confirmed_at");
        assertThat(sql).doesNotContain(
                "INSERT INTO",
                "UPDATE rec_",
                "DELETE FROM");
    }
}
