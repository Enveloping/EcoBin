package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Pattern;

import static org.assertj.core.api.Assertions.assertThat;

class WalletReadMigrationBoundaryTest {

    private static final Pattern PUBLIC_UID_RELATION = Pattern.compile(
            "(?is)(?:UNIQUE|FOREIGN\\s+KEY)\\s*\\([^)]*"
                    + "organization_user_uid[^)]*\\)");

    private static final Path MIGRATION = Path.of(
            "src",
            "main",
            "resources",
            "db",
            "p0-migration",
            "V18__wallet_read_models.sql");

    @Test
    void publicOrganizationUserUidRemainsASnapshotNotAForeignKey()
            throws IOException {
        String sql = Files.readString(
                MIGRATION,
                StandardCharsets.UTF_8);

        assertThat(sql)
                .doesNotContain(
                        "uq_iam_org_user_scope_id_uid",
                        "fk_fund_wallet_entry_user_uid");
        assertThat(PUBLIC_UID_RELATION.matcher(sql).find())
                .as("公开用户 UUID 不能进入唯一键或跨模块外键")
                .isFalse();
    }

    @Test
    void historicalUidBackfillIsCheckedBeforeColumnBecomesRequired()
            throws IOException {
        String sql = Files.readString(
                MIGRATION,
                StandardCharsets.UTF_8);

        assertThat(sql.indexOf(
                "v18_wallet_uid_backfill_guard"))
                .isGreaterThan(sql.indexOf(
                        "UPDATE fund_user_wallet_entry"));
        assertThat(sql.indexOf(
                "MODIFY COLUMN organization_user_uid"))
                .isGreaterThan(sql.indexOf(
                        "v18_wallet_uid_backfill_guard"));
    }
}
