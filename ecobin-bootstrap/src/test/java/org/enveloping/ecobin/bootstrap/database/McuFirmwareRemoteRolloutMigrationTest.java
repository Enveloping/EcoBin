package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;

class McuFirmwareRemoteRolloutMigrationTest {

    private static final Path V55 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V55__mcu_firmware_remote_rollout.sql");

    @Test
    void migrationDefinesTheImmutableReleaseAndFailClosedRolloutShape()
            throws IOException {
        String sql = Files.readString(resolveMigration());

        for (String table : List.of(
                "dev_mcu_firmware_release",
                "dev_mcu_firmware_rollout",
                "dev_mcu_firmware_deployment",
                "dev_mcu_firmware_progress",
                "dev_mcu_firmware_rollout_action")) {
            assertTrue(
                    sql.contains("CREATE TABLE " + table),
                    () -> "V55 must create " + table);
        }
        for (String column : List.of(
                "mcu_firmware_version_code",
                "mcu_firmware_identity_hex",
                "mcu_fixed_frame_revision")) {
            assertTrue(
                    sql.contains("ADD COLUMN " + column),
                    () -> "V55 must project " + column + " on the asset");
        }
        assertTrue(sql.contains("uq_dev_mcu_release_package"));
        assertTrue(sql.contains("uq_dev_mcu_progress_inbox"));
        assertTrue(sql.contains("fk_dev_mcu_progress_inbox"));
        assertTrue(sql.contains("REFERENCES ops_inbox_message (id)"));
        assertTrue(sql.contains(
                "hardware_compatibility = 'ECOBIN_MAINBOARD_V1.1'"));
        assertTrue(sql.contains("'PACKAGE_FETCH_FAILED'"));
        assertTrue(sql.contains("'FAILED_LOCKED'"));
        assertTrue(sql.contains("'AWAITING_PROMOTION'"));
        assertTrue(sql.contains("'ADVANCE_WAVE'"));
    }

    private static Path resolveMigration() {
        if (Files.isRegularFile(V55)) {
            return V55;
        }
        return Path.of("ecobin-bootstrap").resolve(V55);
    }
}
