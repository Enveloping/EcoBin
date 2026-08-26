package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class McuRemoteUpdateCapabilityMigrationTest {

    private static final Path V60 = Path.of(
            "src", "main", "resources", "db", "p0-migration",
            "V60__mcu_remote_update_capability.sql");

    @Test
    void keepsLegacyFactsUnknownAndRequiresExplicitV4Capability()
            throws IOException {
        String sql = Files.readString(resolve(V60), StandardCharsets.UTF_8)
                .replaceAll("\\s+", " ")
                .toLowerCase();

        assertThat(sql).contains(
                "alter table dev_device_acceptance_evidence",
                "add column mcu_remote_update_capable tinyint null",
                "evidence_schema_version < 4 and mcu_remote_update_capable is null",
                "evidence_schema_version >= 4 "
                        + "and mcu_remote_update_capable is not null "
                        + "and mcu_remote_update_capable in (0, 1)",
                "alter table dev_device_asset",
                "mcu_remote_update_capable is null or mcu_remote_update_capable in (0, 1)");
        assertThat(sql).doesNotContain(
                "update dev_device_acceptance_evidence",
                "update dev_device_asset",
                "default 0",
                "default 1");
    }

    private static Path resolve(Path path) {
        if (Files.isRegularFile(path)) {
            return path;
        }
        return Path.of("ecobin-bootstrap").resolve(path);
    }
}
