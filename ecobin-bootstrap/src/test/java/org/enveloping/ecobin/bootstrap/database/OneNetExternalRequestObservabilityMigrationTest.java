package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class OneNetExternalRequestObservabilityMigrationTest {

    private static final Path V61 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V61__onenet_external_request_observability.sql");

    @Test
    void addsNullableBoundedAsciiRequestIdentityWithoutChangingTables()
            throws Exception {
        String sql = Files.readString(resolve(V61), StandardCharsets.UTF_8)
                .replaceAll("\\s+", " ")
                .trim();

        assertThat(sql)
                .contains("ALTER TABLE ops_task_attempt")
                .contains("ADD COLUMN external_request_id VARCHAR(128) "
                        + "CHARACTER SET ascii COLLATE ascii_bin NULL")
                .contains("external_request_id REGEXP "
                        + "'^[A-Za-z0-9._:-]{1,128}$'")
                .contains("external_request_id IS NULL OR ( "
                        + "result_recorded_at IS NOT NULL")
                .contains("CONSTRAINT "
                        + "ck_ops_attempt_external_request_v61")
                .doesNotContain("CREATE TABLE")
                .doesNotContain("UPDATE ")
                .doesNotContain("DEFAULT")
                .doesNotContain("UNIQUE")
                .doesNotContain("INDEX");
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
