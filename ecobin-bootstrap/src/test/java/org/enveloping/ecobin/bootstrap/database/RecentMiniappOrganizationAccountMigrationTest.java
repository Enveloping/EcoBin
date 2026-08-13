package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RecentMiniappOrganizationAccountMigrationTest {

    private static final Path V49 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V49__recent_miniapp_organization_account.sql");

    @Test
    void backfillsBothSessionFamiliesAndReplacesRegistrationOrdering()
            throws Exception {
        String sql = Files.readString(resolve(), StandardCharsets.UTF_8);

        assertTrue(sql.contains("ADD COLUMN last_login_at DATETIME(3) NULL"));
        assertTrue(sql.contains("FROM iam_organization_user_session"));
        assertTrue(sql.contains("FROM iam_staff_login_session"));
        assertTrue(sql.contains("JOIN iam_staff_miniapp_binding"));
        assertTrue(sql.contains("MAX(issued_at) AS last_ordinary_login_at"));
        assertTrue(sql.contains("MAX(session_row.issued_at) AS last_management_login_at"));
        assertTrue(sql.contains("user_row.registered_at"));
        assertTrue(sql.contains("MODIFY COLUMN last_login_at DATETIME(3) NOT NULL"));
        assertTrue(sql.contains("ck_iam_org_user_last_login_v49"));
        assertTrue(sql.contains("DROP INDEX ix_iam_org_user_subject_recent"));
        assertTrue(sql.contains("ix_iam_org_user_subject_recent_login"));
        assertFalse(sql.contains("ALTER COLUMN registered_at"));
    }

    private static Path resolve() {
        if (Files.isRegularFile(V49)) {
            return V49;
        }
        Path nested = Path.of("ecobin-bootstrap").resolve(V49);
        assertTrue(Files.isRegularFile(nested));
        return nested;
    }
}
