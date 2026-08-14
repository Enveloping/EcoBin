package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MerchantTransferAuthorizationCreateRejectionMigrationTest {

    private static final Path V51 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V51__merchant_transfer_authorization_create_rejection.sql");

    @Test
    void releasesOnlyDefinitivelyRejectedCreateRowsAndPreservesHistory()
            throws Exception {
        String sql = Files.readString(resolve(), StandardCharsets.UTF_8);

        assertTrue(sql.contains("SET local_state = 'CREATE_REJECTED'"));
        assertTrue(sql.contains(
                "'PARAM_ERROR', 'NO_AUTH', 'SIGN_ERROR', 'MCHID_MISMATCH'"));
        assertTrue(sql.contains("SET local_state = 'UNKNOWN'"));
        assertTrue(sql.contains(
                "'SIGNATURE_ERROR', 'RESPONSE_SIGNATURE_INVALID'"));
        assertTrue(sql.contains("local_state = 'CREATE_REJECTED'"));
        assertTrue(sql.contains("last_api_error_code IS NOT NULL"));
        assertTrue(sql.indexOf("ADD CONSTRAINT ck_fund_transfer_authorization_state")
                        < sql.indexOf("SET local_state = 'CREATE_REJECTED'"),
                "the expanded check must exist before rows enter the new state");
        assertFalse(sql.contains(
                "DELETE FROM fund_wechat_transfer_authorization"));
        assertFalse(sql.contains("user_display_name_snapshot ="));
        assertFalse(sql.contains("out_authorization_no ="));
    }

    private static Path resolve() {
        if (Files.isRegularFile(V51)) {
            return V51;
        }
        Path nested = Path.of("ecobin-bootstrap").resolve(V51);
        assertTrue(Files.isRegularFile(nested));
        return nested;
    }
}
