package org.enveloping.ecobin.operations.application.reliability;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;

import java.sql.Connection;
import java.sql.DriverManager;
import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@EnabledIfEnvironmentVariable(
        named = "ECOBIN_V53_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class FundsOperationalControlMysqlIntegrationTest {

    private static final long TENANT_ID = 900000000000011L;
    private static final long ORGANIZATION_ID = 900000000000012L;
    private static final String SOURCE_KEY =
            "AUTO_WITHDRAWAL_LIQUIDITY:"
                    + TENANT_ID + ":" + ORGANIZATION_ID;
    private static final LocalDateTime FIRST_SEEN =
            LocalDateTime.parse("2026-08-17T12:34:56.789");

    @Test
    void resolvesCoveredShortageAndReopensARecurringCondition()
            throws Exception {
        try (Connection connection = DriverManager.getConnection(
                requiredEnvironment("ECOBIN_V53_MYSQL_URL"),
                requiredEnvironment("ECOBIN_V53_MYSQL_USERNAME"),
                requiredEnvironment("ECOBIN_V53_MYSQL_PASSWORD"))) {
            connection.setAutoCommit(false);
            try {
                JdbcTemplate jdbc = new JdbcTemplate(
                        new SingleConnectionDataSource(connection, true));
                jdbc.execute("SET FOREIGN_KEY_CHECKS = 0");
                FundsOperationalControlService service =
                        new FundsOperationalControlService(jdbc);

                service.observeAutoWithdrawalOrganizationLiquidityShortage(
                        TENANT_ID, ORGANIZATION_ID, 100L, FIRST_SEEN);
                assertThat(status(jdbc)).isEqualTo("OPEN");

                service.resolveAutoWithdrawalOrganizationLiquidityShortageIfCovered(
                        TENANT_ID, ORGANIZATION_ID, 99L,
                        FIRST_SEEN.plusMinutes(1));
                assertThat(status(jdbc)).isEqualTo("OPEN");

                service.resolveAutoWithdrawalOrganizationLiquidityShortageIfCovered(
                        TENANT_ID, ORGANIZATION_ID, 100L,
                        FIRST_SEEN.plusMinutes(2));
                assertThat(status(jdbc)).isEqualTo("RESOLVED");

                jdbc.update("""
                        UPDATE ops_alert
                        SET acknowledged_at = ?,
                            acknowledged_audit_id = 900000000000013,
                            updated_at = ?
                        WHERE source_kind = 'DOMAIN_FACT'
                          AND source_type = 'ORGANIZATION_PAYOUT_ACCOUNT'
                          AND source_key = ?
                        """, FIRST_SEEN.plusMinutes(3),
                        FIRST_SEEN.plusMinutes(3), SOURCE_KEY);
                service.observeAutoWithdrawalOrganizationLiquidityShortage(
                        TENANT_ID, ORGANIZATION_ID, 200L,
                        FIRST_SEEN.plusMinutes(4));

                AlertState reopened = jdbc.queryForObject("""
                        SELECT status, resolved_at, acknowledged_at,
                               acknowledged_audit_id, discovery_count,
                               CAST(JSON_UNQUOTE(JSON_EXTRACT(
                                   safe_display_parameters,
                                   '$.requiredAmountCent')) AS UNSIGNED)
                                   AS required_amount_cent
                        FROM ops_alert
                        WHERE source_kind = 'DOMAIN_FACT'
                          AND source_type = 'ORGANIZATION_PAYOUT_ACCOUNT'
                          AND source_key = ?
                        """, (resultSet, ignored) -> new AlertState(
                                resultSet.getString("status"),
                                resultSet.getObject("resolved_at"),
                                resultSet.getObject("acknowledged_at"),
                                resultSet.getObject("acknowledged_audit_id"),
                                resultSet.getLong("discovery_count"),
                                resultSet.getLong("required_amount_cent")),
                        SOURCE_KEY);
                assertThat(reopened).isEqualTo(new AlertState(
                        "OPEN", null, null, null, 2L, 200L));
            } finally {
                connection.rollback();
            }
        }
    }

    private static String status(JdbcTemplate jdbc) {
        return jdbc.queryForObject("""
                SELECT status
                FROM ops_alert
                WHERE source_kind = 'DOMAIN_FACT'
                  AND source_type = 'ORGANIZATION_PAYOUT_ACCOUNT'
                  AND source_key = ?
                """, String.class, SOURCE_KEY);
    }

    private static String requiredEnvironment(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(name + " must be configured");
        }
        return value;
    }

    private record AlertState(
            String status,
            Object resolvedAt,
            Object acknowledgedAt,
            Object acknowledgedAuditId,
            long discoveryCount,
            long requiredAmountCent) {
    }
}
