package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class BusinessRuntimeImageBridgeBaselineMigrationTest {

    private static final Path V67 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V67__business_runtime_image_bridge_baseline.sql");

    @Test
    void distinguishesPackagedReleaseFromOneTimeImageBridge()
            throws IOException {
        String sql = Files.readString(resolve(V67), StandardCharsets.UTF_8)
                .replace("\r\n", "\n");

        assertThat(sql).contains(
                "ADD COLUMN source_business_baseline_kind VARCHAR(24)",
                "MODIFY COLUMN source_business_release_uid CHAR(36)",
                "MODIFY COLUMN source_business_release_sequence BIGINT UNSIGNED NULL",
                "ADD CONSTRAINT ck_dev_edge_deployment_source_baseline CHECK",
                "source_business_baseline_kind = 'BUSINESS_RELEASE'",
                "source_business_release_uid IS NOT NULL",
                "source_business_baseline_kind = 'IMAGE_BRIDGE'",
                "source_business_release_uid IS NULL",
                "source_business_release_sequence IS NULL",
                "ADD CONSTRAINT ck_dev_edge_deployment_state CHECK")
                .doesNotContain(
                        "UPDATE dev_edge_software_deployment",
                        "active_business_release_uid =",
                        "active_business_release_sequence =");
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
