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
        named = "ECOBIN_V54_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class DeliveryAutoReviewAmountLimitMysqlConstraintTest {

    @Test
    void mysqlRejectsAutomaticRulesWithoutAnAmountCeiling()
            throws Exception {
        try (Connection connection = DriverManager.getConnection(
                requiredEnvironment("ECOBIN_V54_MYSQL_URL"),
                requiredEnvironment("ECOBIN_V54_MYSQL_USERNAME"),
                requiredEnvironment("ECOBIN_V54_MYSQL_PASSWORD"))) {
            connection.setAutoCommit(false);
            try {
                connection.createStatement()
                        .execute("SET FOREIGN_KEY_CHECKS = 0");
                try (PreparedStatement insert = connection.prepareStatement("""
                        INSERT INTO rec_organization_delivery_config (
                            tenant_id, organization_id, version_no,
                            content_sha256, review_mode,
                            automatic_review_max_amount_cent,
                            open_balance_floor_cent,
                            max_review_abs_weight_g,
                            publication_source,
                            published_by_staff_account_id,
                            published_at, created_at
                        ) VALUES (
                            900000000000001, 900000000000002, 1,
                            UNHEX(REPEAT('00', 32)),
                            'NORMAL_AUTO_IMMEDIATE', NULL,
                            -1000, 100000,
                            'SYSTEM', NULL,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """)) {
                    SQLException failure = assertThrows(
                            SQLException.class,
                            insert::executeUpdate);

                    assertThat(failure.getErrorCode()).isEqualTo(3819);
                    assertThat(failure.getMessage())
                            .contains("ck_rec_delivery_config_v54");
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
