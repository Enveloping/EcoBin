package org.enveloping.ecobin.funds.application.walletquery;

import org.enveloping.ecobin.funds.api.query.PersonalWalletEntryAudience;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JdbcWalletReadRepositoryTest {

    private static final long TENANT_ID = 41L;
    private static final long ORGANIZATION_ID = 43L;
    private static final long WALLET_ID = 47L;

    @Mock
    private JdbcTemplate jdbc;

    private JdbcWalletReadRepository repository;

    @BeforeEach
    void setUp() {
        repository = new JdbcWalletReadRepository(jdbc);
    }

    @Test
    @SuppressWarnings("unchecked")
    void ordinaryUserExcludesWithdrawalTransfersBeforePaging() {
        ArgumentCaptor<String> sql =
                ArgumentCaptor.forClass(String.class);
        when(jdbc.query(
                sql.capture(),
                any(RowMapper.class),
                eq(TENANT_ID),
                eq(ORGANIZATION_ID),
                eq(WALLET_ID),
                eq("WITHDRAWAL_FREEZE"),
                eq("WITHDRAWAL_RELEASED"),
                eq(21)))
                .thenReturn(List.of());

        repository.findPersonalEntries(
                TENANT_ID,
                ORGANIZATION_ID,
                WALLET_ID,
                PersonalWalletEntryAudience.ORDINARY_USER,
                null,
                21);

        assertThat(sql.getValue())
                .contains("AND event_type NOT IN (?, ?)")
                .contains("ORDER BY entry_sequence_no DESC");
        assertThat(sql.getValue().indexOf(
                "AND event_type NOT IN (?, ?)"))
                .isLessThan(sql.getValue().indexOf("ORDER BY"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void auditViewKeepsTheCompleteImmutableLedger() {
        ArgumentCaptor<String> sql =
                ArgumentCaptor.forClass(String.class);
        when(jdbc.query(
                sql.capture(),
                any(RowMapper.class),
                eq(TENANT_ID),
                eq(ORGANIZATION_ID),
                eq(WALLET_ID),
                eq(21)))
                .thenReturn(List.of());

        repository.findPersonalEntries(
                TENANT_ID,
                ORGANIZATION_ID,
                WALLET_ID,
                PersonalWalletEntryAudience.AUDIT,
                null,
                21);

        assertThat(sql.getValue())
                .doesNotContain("event_type NOT IN")
                .contains("ORDER BY entry_sequence_no DESC");
    }
}
