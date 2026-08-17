package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.SQLException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;

@EnabledIfEnvironmentVariable(
        named = "ECOBIN_V53_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class DeliveryAutoReviewWithdrawalMysqlConstraintTest {

    @Test
    void mysqlRejectsEnabledAutomationWithNullAmounts() throws Exception {
        try (Connection connection = DriverManager.getConnection(
                requiredEnvironment("ECOBIN_V53_MYSQL_URL"),
                requiredEnvironment("ECOBIN_V53_MYSQL_USERNAME"),
                requiredEnvironment("ECOBIN_V53_MYSQL_PASSWORD"))) {
            connection.setAutoCommit(false);
            try {
                connection.createStatement()
                        .execute("SET FOREIGN_KEY_CHECKS = 0");
                try (PreparedStatement insert = connection.prepareStatement("""
                        INSERT INTO fund_organization_withdraw_config (
                            tenant_id, organization_id, version_no,
                            content_sha256, hard_limit_cent,
                            manual_min_cent, manual_max_cent,
                            manual_review_free_threshold_cent,
                            auto_withdrawal_enabled,
                            auto_min_cent, auto_max_cent,
                            auto_review_free_threshold_cent,
                            publication_source,
                            published_by_staff_account_id,
                            published_at, created_at
                        ) VALUES (
                            900000000000001, 900000000000002, 1,
                            UNHEX(REPEAT('00', 32)), 1000,
                            10, 1000, 0,
                            1, NULL, NULL, NULL,
                            'SYSTEM', NULL,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """)) {
                    SQLException failure = assertThrows(
                            SQLException.class,
                            insert::executeUpdate);

                    assertThat(failure.getErrorCode()).isEqualTo(3819);
                    assertThat(failure.getMessage())
                            .contains("ck_fund_withdraw_config_v53");
                }
            } finally {
                connection.rollback();
            }
        }
    }

    private static String requiredEnvironment(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(name + " must be configured");
        }
        return value;
    }
}
