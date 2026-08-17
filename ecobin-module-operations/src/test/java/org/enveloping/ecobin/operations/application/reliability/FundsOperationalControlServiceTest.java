package org.enveloping.ecobin.operations.application.reliability;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class FundsOperationalControlServiceTest {

    private static final LocalDateTime NOW =
            LocalDateTime.parse("2026-08-17T12:34:56.789");

    @Test
    void recurringLiquidityShortageReopensResolvedAggregate() {
        RecordingJdbcTemplate jdbc = new RecordingJdbcTemplate();
        FundsOperationalControlService service =
                new FundsOperationalControlService(jdbc);

        service.observeAutoWithdrawalOrganizationLiquidityShortage(
                11L,
                22L,
                100L,
                NOW);

        String duplicateUpdate = jdbc.sql()
                .toUpperCase(Locale.ROOT)
                .substring(jdbc.sql().toUpperCase(Locale.ROOT)
                        .indexOf("ON DUPLICATE KEY UPDATE"));
        assertThat(duplicateUpdate).contains(
                "STATUS = 'OPEN'",
                "RESOLVED_AT = NULL",
                "ACKNOWLEDGED_AT = NULL",
                "ACKNOWLEDGED_AUDIT_ID = NULL");
    }

    @Test
    void resolvesOpenShortageOnlyWhenStoredRequiredAmountIsCovered() {
        RecordingJdbcTemplate jdbc = new RecordingJdbcTemplate();
        FundsOperationalControlService service =
                new FundsOperationalControlService(jdbc);

        service.resolveAutoWithdrawalOrganizationLiquidityShortageIfCovered(
                11L,
                22L,
                150L,
                NOW);

        assertThat(jdbc.sql().toUpperCase(Locale.ROOT)).contains(
                "STATUS = 'RESOLVED'",
                "STATUS = 'OPEN'",
                "JSON_EXTRACT",
                "'$.REQUIREDAMOUNTCENT'",
                "AS UNSIGNED) <= ?");
        assertThat(jdbc.arguments()).containsExactly(
                NOW,
                NOW,
                11L,
                22L,
                "AUTO_WITHDRAWAL_LIQUIDITY:11:22",
                150L);
    }

    private static final class RecordingJdbcTemplate extends JdbcTemplate {

        private String sql;
        private List<Object> arguments;

        @Override
        public int update(String sql, Object... args) {
            this.sql = sql;
            this.arguments = Arrays.asList(args);
            return 1;
        }

        String sql() {
            return sql;
        }

        List<Object> arguments() {
            return arguments;
        }
    }
}
