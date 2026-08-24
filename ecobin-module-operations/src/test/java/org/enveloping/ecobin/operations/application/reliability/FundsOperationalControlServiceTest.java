package org.enveloping.ecobin.operations.application.reliability;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

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

    @Test
    @SuppressWarnings("unchecked")
    void trustedQueryProofCompletesTheExactBlockedCreateTask()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        var row = mock(java.sql.ResultSet.class);
        when(row.getLong("id")).thenReturn(91L);
        when(row.getString("state")).thenReturn("BLOCKED");
        when(row.getString("lease_token")).thenReturn("stale-lease");
        when(row.getString("dispatch_wait_reason")).thenReturn(
                "AUTO_RETRY_EXHAUSTED");
        when(row.getLong("wake_version")).thenReturn(7L);
        when(jdbc.query(
                contains("target_type = 'WECHAT_TRANSFER_AUTHORIZATION'"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        when(jdbc.update(
                contains("SET state = 'DONE'"),
                any(Object[].class))).thenReturn(1);
        FundsOperationalControlService service =
                new FundsOperationalControlService(jdbc);

        service.completeMerchantTransferAuthorizationCreateFromQueryProof(
                11L, 22L, "AW20000000000040008000000000000001", NOW);

        verify(jdbc).update(
                contains("WHERE id = ? AND state IN ('PENDING', 'BLOCKED')"),
                any(Object[].class));
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
