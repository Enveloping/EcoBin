package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class DeviceEntryUrlMcuApplicationEvidenceMigrationTest {

    private static final Path V82 = Path.of(
            "src", "main", "resources", "db", "p0-migration",
            "V82__device_entry_url_mcu_application_evidence.sql");

    @Test
    void preservesLegacyEvidenceAndRequiresAtomicQueueProofForV5()
            throws IOException {
        String sql = Files.readString(resolve(V82), StandardCharsets.UTF_8)
                .replaceAll("\\s+", " ")
                .toLowerCase();

        assertThat(sql).contains(
                "add column device_entry_url_mcu_applied tinyint null",
                "add column device_entry_url_applied_sha256 binary(32) null",
                "add column device_entry_url_applied_mcu_boot_id bigint null",
                "add column device_entry_url_display_basis varchar(48)",
                "evidence_schema_version < 5",
                "device_entry_url_mcu_applied is null",
                "evidence_schema_version >= 5",
                "device_entry_url_mcu_applied = 1",
                "device_entry_url_applied_mcu_boot_id > 0",
                "'uart3_command_atomically_queued'",
                "device_entry_url_mcu_applied = 0",
                "'not_applied'",
                "evidence_schema_version < 5 or");
        assertThat(sql).doesNotContain(
                "update dev_device_acceptance_evidence",
                "default 0",
                "default 1",
                "usart_flag_tc",
                "hmi_ack");
    }

    private static Path resolve(Path path) {
        if (Files.isRegularFile(path)) {
            return path;
        }
        return Path.of("ecobin-bootstrap").resolve(path);
    }
}
