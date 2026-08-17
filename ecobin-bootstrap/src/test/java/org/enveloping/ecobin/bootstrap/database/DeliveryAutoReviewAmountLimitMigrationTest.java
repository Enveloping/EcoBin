package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class DeliveryAutoReviewAmountLimitMigrationTest {

    private static final Path V54 = Path.of(
            "src/main/resources/db/p0-migration",
            "V54__delivery_auto_review_amount_limit.sql");

    @Test
    void failsBeforeDdlWhenLegacyAutomaticRulesNeedAnExplicitDecision()
            throws Exception {
        String sql = sql();

        int assertionCall = sql.indexOf("PREPARE P0_V54_GUARD");
        int firstAlter = sql.indexOf("ALTER TABLE");
        assertThat(assertionCall).isGreaterThanOrEqualTo(0);
        assertThat(firstAlter).isGreaterThan(assertionCall);
        assertThat(sql).contains(
                "REVIEW_MODE <> 'ALL_MANUAL'",
                "REVIEW_MODE_SNAPSHOT <> 'ALL_MANUAL'",
                "P0_V54_BLOCKED_LEGACY_AUTO_REVIEW_REQUIRES_AMOUNT_LIMIT",
                "DEALLOCATE PREPARE P0_V54_GUARD");
        assertThat(sql)
                .doesNotContain("CREATE PROCEDURE")
                .doesNotContain("CREATE FUNCTION");
    }

    @Test
    void freezesTheAmountCeilingInConfigurationAndNewOrders()
            throws Exception {
        String sql = sql();

        assertThat(sql).contains(
                "AUTOMATIC_REVIEW_MAX_AMOUNT_CENT BIGINT NULL",
                "AUTOMATIC_REVIEW_MAX_AMOUNT_CENT_SNAPSHOT BIGINT NULL",
                "REVIEW_MODE = 'ALL_MANUAL'",
                "AUTOMATIC_REVIEW_MAX_AMOUNT_CENT IS NULL",
                "REVIEW_MODE <> 'ALL_MANUAL'",
                "AUTOMATIC_REVIEW_MAX_AMOUNT_CENT IS NOT NULL",
                "AUTOMATIC_REVIEW_MAX_AMOUNT_CENT >= 0",
                "AUTOMATIC_REVIEW_MAX_AMOUNT_CENT_SNAPSHOT IS NOT NULL");
        assertThat(sql)
                .doesNotContain("UPDATE REC_ORGANIZATION_DELIVERY_CONFIG")
                .doesNotContain("UPDATE REC_DELIVERY_ORDER")
                .doesNotContain(
                        "DROP FOREIGN KEY FK_REC_DELIVERY_ORDER_REVIEW_CONFIG")
                .doesNotContain("DROP INDEX UQ_REC_DELIVERY_CONFIG_REVIEW_REF");
    }

    private static String sql() throws Exception {
        return Files.readString(resolve(), StandardCharsets.UTF_8)
                .toUpperCase(Locale.ROOT);
    }

    private static Path resolve() {
        if (Files.isRegularFile(V54)) {
            return V54;
        }
        return Path.of("ecobin-bootstrap").resolve(V54);
    }
}
