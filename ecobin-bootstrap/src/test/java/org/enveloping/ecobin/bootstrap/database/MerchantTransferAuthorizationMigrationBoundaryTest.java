package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class MerchantTransferAuthorizationMigrationBoundaryTest {

    private static final Path V35 = Path.of(
            "src",
            "main",
            "resources",
            "db",
            "p0-migration",
            "V35__merchant_transfer_authorization.sql");

    @Test
    void authorizationIdentityIsStrongAndUnknownStateKeepsCurrentSlot()
            throws IOException {
        String sql = Files.readString(V35, StandardCharsets.UTF_8);

        assertThat(sql).contains(
                "uq_iam_org_user_transfer_authorization_ref",
                "fund_wechat_transfer_authorization",
                "fund_wechat_transfer_authorization_observation",
                "uq_fund_transfer_authorization_current",
                "'CREATED',\n                    'WAIT_USER_CONFIRM',\n"
                        + "                    'ACTIVE',\n"
                        + "                    'UNKNOWN'",
                "local_state = 'EXPIRED'",
                "last_api_error_code = 'NOT_FOUND'",
                "USER_OVERDUE_UNCONFIRMED_AFTER_RETENTION",
                "fk_fund_transfer_authorization_recipient",
                "observed_user_recv_perception")
                .doesNotContain(
                        "state_conflict = 0 OR local_state = 'UNKNOWN'",
                        "DROP INDEX uq_iam_org_user_appid_openid");
    }

    @Test
    void legacyRowsRemainUserConfirmAndAuthorizedRowsRequireOneIdentity()
            throws IOException {
        String sql = Files.readString(V35, StandardCharsets.UTF_8);

        assertThat(sql).contains(
                "NOT NULL DEFAULT 'USER_CONFIRM'",
                "collection_mode_snapshot = 'USER_CONFIRM'",
                "collection_mode_snapshot = 'AUTHORIZED'",
                "transfer_authorization_id IS NOT NULL",
                "out_authorization_no_snapshot IS NOT NULL",
                "authorization_id_snapshot IS NOT NULL",
                "fk_fund_wechat_transfer_withdrawal_mode",
                "fk_fund_wechat_transfer_authorization");
    }

    @Test
    void authorizedTransferCannotCarryPerOrderConfirmationRequestFields()
            throws IOException {
        String sql = Files.readString(V35, StandardCharsets.UTF_8);

        assertThat(sql).contains(
                "transfer_page_style_snapshot IS NULL",
                "notify_url_snapshot IS NULL",
                "notify_url_sha256 IS NULL");
    }
}
