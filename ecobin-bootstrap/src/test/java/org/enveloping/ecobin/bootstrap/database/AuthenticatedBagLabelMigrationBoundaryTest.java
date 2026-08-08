package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class AuthenticatedBagLabelMigrationBoundaryTest {

    private static final Path V43 = Path.of(
            "src",
            "main",
            "resources",
            "db",
            "p0-migration",
            "V43__authenticated_bag_label_batches.sql");

    @Test
    void addsOnlyDisposablePlatformPrintHistoryWithoutPreRegisteringBags()
            throws IOException {
        String sql = Files.readString(V43, StandardCharsets.UTF_8);

        assertThat(sql).contains(
                "CREATE TABLE rec_bag_label_batch",
                "CREATE TABLE rec_bag_label_item",
                "created_by_platform_admin_id",
                "ON DELETE CASCADE",
                "^EB1_K[0-9A-Z]{1,6}_");
        assertThat(sql).doesNotContain(
                "INSERT INTO rec_bag",
                "tenant_id",
                "organization_id");
    }
}
