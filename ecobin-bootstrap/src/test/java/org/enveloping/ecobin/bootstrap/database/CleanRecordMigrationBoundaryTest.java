package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class CleanRecordMigrationBoundaryTest {

    private static final Path V20 = migration(
            "V20__clean_records_without_review.sql");
    private static final Path V21 = migration(
            "V21__clean_normal_flow_facts.sql");

    @Test
    void cleaningReviewProjectionIsReplacedByEditableRecordHistory()
            throws IOException {
        String sql = Files.readString(V20, StandardCharsets.UTF_8);

        assertThat(sql)
                .contains(
                        "DROP TABLE rec_clean_revision",
                        "CREATE TABLE rec_clean_record_change",
                        "effective_removed_net_weight_g",
                        "record_remark",
                        "lock_version",
                        "trg_rec_clean_record_change_no_update",
                        "trg_rec_clean_record_change_no_delete",
                        "'clean.edit'")
                .doesNotContain("clean.reward", "clean.points");
    }

    @Test
    void fixedFrameCompletionPreservesLegacyDoorEvidence()
            throws IOException {
        String sql = Files.readString(V21, StandardCharsets.UTF_8);

        assertThat(sql)
                .contains(
                        "edge_saved_confirmed",
                        "first_unlock_may_have_executed",
                        "clean_lock_deenergized_confirmed",
                        "cleaner_physical_close_confirmed",
                        "clean_solenoid_health IN ('OK', 'UNKNOWN')",
                        "clean_door_state_basis = 'CLEANER_CONFIRMATION'",
                        "cleaner_physical_close_confirmed IS NULL",
                        "clean_action_sequence IS NULL",
                        "clean_door_inferred_state = 'CLOSED'",
                        "clean_door_state_basis = 'INFERRED_FROM_LOCK_POWER'",
                        "MODIFY COLUMN device_occurred_at DATETIME(3) NULL")
                .doesNotContain(
                        "cleaner_physical_close_confirmed = 1,\n"
                                + "    clean_action_sequence = 1,\n"
                                + "    clean_door_inferred_state = 'UNKNOWN',\n"
                                + "    clean_door_state_basis = "
                                + "'CLEANER_CONFIRMATION'");
    }

    private static Path migration(String file) {
        return Path.of(
                "src",
                "main",
                "resources",
                "db",
                "p0-migration",
                file);
    }
}
