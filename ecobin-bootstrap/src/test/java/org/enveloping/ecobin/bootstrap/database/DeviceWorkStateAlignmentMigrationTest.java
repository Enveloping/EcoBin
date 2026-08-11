package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class DeviceWorkStateAlignmentMigrationTest {

    private static final Path V47 = Path.of(
            "src/main/resources/db/p0-migration",
            "V47__device_work_state_alignment.sql");

    @Test
    void addsAResultlessTechnicalBaselineTerminalAndGenerationIndex()
            throws Exception {
        String sql = Files.readString(V47, StandardCharsets.UTF_8)
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "STATUS = 'TECHNICAL_ABORTED'",
                        "PHYSICAL_RESULT_ID IS NULL",
                        "FAULT_CODE IS NOT NULL",
                        "IX_REC_BASELINE_MEASUREMENT_GENERATION",
                        "DEVICE_CONFIG_VERSION_ID",
                        "INITIATOR_KIND");
    }
}
