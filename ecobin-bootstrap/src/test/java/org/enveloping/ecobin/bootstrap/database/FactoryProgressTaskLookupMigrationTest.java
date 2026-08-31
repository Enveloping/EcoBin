package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class FactoryProgressTaskLookupMigrationTest {

    private static final Path V62 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V62__factory_progress_task_lookup.sql");

    @Test
    void addsOnlyTheCurrentFactoryProgressTaskLookupIndex()
            throws Exception {
        String sql = Files.readString(resolve(V62), StandardCharsets.UTF_8)
                .replaceAll("\\s+", " ")
                .trim();

        assertThat(sql)
                .contains("ALTER TABLE ops_reliable_task")
                .contains("ADD INDEX ix_ops_task_factory_progress ( "
                        + "source_device_asset_id, task_type, id DESC )")
                .doesNotContain("CREATE TABLE")
                .doesNotContain("ADD COLUMN")
                .doesNotContain("UPDATE ")
                .doesNotContain("DELETE ")
                .doesNotContain("UNIQUE");
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
