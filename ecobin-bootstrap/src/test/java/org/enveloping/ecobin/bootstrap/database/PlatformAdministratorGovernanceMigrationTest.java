package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PlatformAdministratorGovernanceMigrationTest {

    private static final Path V50 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V50__platform_administrator_governance.sql");

    @Test
    void marksOnlyTheKnownSoleLegacyAccountAndAddsPermanentLifecycleGuards()
            throws Exception {
        String sql = Files.readString(resolve(), StandardCharsets.UTF_8);

        assertTrue(sql.contains("ADD COLUMN admin_kind VARCHAR(16)"));
        assertTrue(sql.contains("ADD COLUMN deleted_at DATETIME(3) NULL"));
        assertTrue(sql.contains("@v50_platform_admin_count = 1"));
        assertTrue(sql.contains("login_name = 'enveloping'"));
        assertTrue(sql.contains("SET admin_kind = 'DEFAULT'"));
        assertTrue(sql.contains("uq_iam_platform_admin_default_slot"));
        assertTrue(sql.contains("ck_iam_platform_admin_default_v50"));
        assertTrue(sql.contains("ck_iam_platform_admin_deleted_v50"));
        assertFalse(sql.contains("password_hash ="));
        assertFalse(sql.contains("DELETE FROM iam_platform_admin"));
    }

    private static Path resolve() {
        if (Files.isRegularFile(V50)) {
            return V50;
        }
        Path nested = Path.of("ecobin-bootstrap").resolve(V50);
        assertTrue(Files.isRegularFile(nested));
        return nested;
    }
}
