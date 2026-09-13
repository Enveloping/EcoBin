package org.enveloping.ecobin;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class OfflineOccupancyReleaseMigrationTest {

    @Test
    void migrationAddsOnlyAuditableReleaseFactsAndConservativeBackfill()
            throws IOException {
        Path root = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath().normalize().getParent();
        String sql = Files.readString(root.resolve(
                "ecobin-bootstrap/src/main/resources/db/p0-migration/"
                        + "V80__offline_occupancy_release.sql"));

        assertThat(sql)
                .contains(
                        "dev_device_transport_state",
                        "offline_since_at DATETIME(3) NULL",
                        "dev_delivery_session",
                        "rec_clean_operation",
                        "offline_occupancy_released_at DATETIME(3) NULL",
                        "SET offline_since_at = UTC_TIMESTAMP(3)")
                .doesNotContain("DELETE FROM")
                .doesNotContain("UPDATE dev_delivery_session")
                .doesNotContain("UPDATE rec_clean_operation");
    }
}
