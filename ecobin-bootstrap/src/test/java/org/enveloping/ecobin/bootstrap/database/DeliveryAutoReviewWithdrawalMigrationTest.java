package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class DeliveryAutoReviewWithdrawalMigrationTest {

    private static final Path V53 = Path.of(
            "src/main/resources/db/p0-migration",
            "V53__delivery_auto_review_and_withdrawal.sql");

    @Test
    void keepsExistingOrganizationsSafeAndAddsImmutableAutomationFacts()
            throws Exception {
        String sql = Files.readString(resolve(), StandardCharsets.UTF_8)
                .toUpperCase(Locale.ROOT);

        assertThat(sql).contains(
                "NORMAL_AUTO_IMMEDIATE",
                "NORMAL_AUTO_AFTER_24H",
                "NORMAL_AUTO_AFTER_48H",
                "REVIEW_MODE_SNAPSHOT",
                "AUTOMATIC_REVIEW_DUE_AT",
                "REVIEWER_KIND = 'SYSTEM'",
                "AUTO_WITHDRAWAL_ENABLED TINYINT NOT NULL DEFAULT 0",
                "SOURCE_TYPE",
                "SOURCE_DELIVERY_ORDER_NO",
                "REVIEW_REQUIRED_AT_CREATION",
                "CREATE TABLE FUND_DELIVERY_AUTO_WITHDRAWAL_DECISION",
                "EXECUTION_LANE IN ('DEVICE', 'FUNDS', 'RECYCLING')");
        assertThat(sql).contains(
                "REVIEW_MODE_SNAPSHOT = 'ALL_MANUAL'",
                "AUTOMATIC_REVIEW_DUE_AT IS NULL",
                "OUTCOME = 'SKIPPED'",
                "UNIQUE (DELIVERY_REVISION_ID)");
        assertThat(sql)
                .doesNotContain("UPDATE REC_DELIVERY_ORDER")
                .doesNotContain("UPDATE FUND_WITHDRAWAL_ORDER")
                .doesNotContain("UPDATE FUND_ORGANIZATION_WITHDRAW_CONFIG");
    }

    private static Path resolve() {
        if (Files.isRegularFile(V53)) {
            return V53;
        }
        return Path.of("ecobin-bootstrap").resolve(V53);
    }
}
