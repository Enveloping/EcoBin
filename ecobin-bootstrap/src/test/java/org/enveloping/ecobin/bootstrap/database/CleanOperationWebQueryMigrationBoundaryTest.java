package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class CleanOperationWebQueryMigrationBoundaryTest {

    private static final Path V44 = Path.of(
            "src", "main", "resources", "db", "p0-migration",
            "V44__clean_operation_web_queries.sql");

    @Test
    void addsOnlyOrganizationScopedQueryIndexes() throws IOException {
        String sql = Files.readString(V44, StandardCharsets.UTF_8);

        assertThat(sql).contains(
                "ALTER TABLE rec_clean_operation",
                "ix_rec_clean_operation_created",
                "ix_rec_clean_operation_status_created",
                "tenant_id",
                "organization_id",
                "created_at DESC",
                "id DESC");
        assertThat(sql).doesNotContain(
                "INSERT INTO",
                "UPDATE rec_clean_operation",
                "DELETE FROM");
    }
}
