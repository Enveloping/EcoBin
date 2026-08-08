package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class GlobalMiniappDeviceEntryMigrationBoundaryTest {

    private static final Path V41 = Path.of(
            "src",
            "main",
            "resources",
            "db",
            "p0-migration",
            "V41__global_miniapp_device_entry_url.sql");

    @Test
    void removesThePerChannelEntryUrlWithoutIntroducingAnotherTable()
            throws IOException {
        String sql = Files.readString(V41, StandardCharsets.UTF_8);

        assertThat(sql)
                .contains(
                        "ALTER TABLE iam_miniapp_channel",
                        "DROP CHECK ck_iam_channel_entry_url",
                        "DROP COLUMN entry_base_url")
                .doesNotContain("CREATE TABLE", "ADD COLUMN");
    }
}
