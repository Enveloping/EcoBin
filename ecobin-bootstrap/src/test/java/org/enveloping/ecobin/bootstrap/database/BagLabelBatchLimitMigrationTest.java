package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class BagLabelBatchLimitMigrationTest {

    private static final Path V59 = Path.of(
            "src",
            "main",
            "resources",
            "db",
            "p0-migration",
            "V59__bag_label_batch_limit_500.sql");

    @Test
    void expandsBothExistingChecksWithoutRecreatingOrPreallocatingBags()
            throws IOException {
        String sql = Files.readString(resolve(V59), StandardCharsets.UTF_8)
                .replaceAll("\\s+", " ")
                .toLowerCase();

        assertThat(sql).contains(
                "alter table rec_bag_label_batch",
                "drop check ck_rec_bag_label_batch_count",
                "add constraint ck_rec_bag_label_batch_count_v59",
                "label_count between 1 and 500",
                "alter table rec_bag_label_item",
                "drop check ck_rec_bag_label_item_sequence",
                "add constraint ck_rec_bag_label_item_sequence_v59",
                "sequence_no between 1 and 500");
        assertThat(sql).doesNotContain(
                "create table",
                "insert into rec_bag",
                "tenant_id",
                "organization_id");
    }

    private static Path resolve(Path path) {
        if (Files.isRegularFile(path)) {
            return path;
        }
        return Path.of("ecobin-bootstrap").resolve(path);
    }
}
