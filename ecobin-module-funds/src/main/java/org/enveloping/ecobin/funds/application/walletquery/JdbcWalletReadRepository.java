package org.enveloping.ecobin.funds.application.walletquery;

import org.enveloping.ecobin.funds.api.query.PersonalWalletEntryAudience;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

@Repository
class JdbcWalletReadRepository implements WalletReadRepository {

    private static final String WITHDRAWAL_FREEZE =
            "WITHDRAWAL_FREEZE";
    private static final String WITHDRAWAL_RELEASED =
            "WITHDRAWAL_RELEASED";

    private static final String ENTRY_COLUMNS = """
            SELECT entry_uid,
                   organization_user_uid,
                   entry_sequence_no,
                   event_type,
                   available_delta_cent,
                   frozen_delta_cent,
                   available_after_cent,
                   frozen_after_cent,
                   source_type,
                   source_no,
                   occurred_at
            FROM fund_user_wallet_entry
            """;

    private final JdbcTemplate jdbc;

    JdbcWalletReadRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<WalletRow> findWallet(
            long tenantId,
            long organizationId,
            long organizationUserId) {
        return jdbc.query("""
                        SELECT id,
                               available_balance_cent,
                               frozen_withdrawal_cent,
                               lock_version
                        FROM fund_user_wallet
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND organization_user_id = ?
                        """,
                (rs, ignored) -> new WalletRow(
                        rs.getLong("id"),
                        rs.getLong("available_balance_cent"),
                        rs.getLong("frozen_withdrawal_cent"),
                        rs.getLong("lock_version")),
                tenantId,
                organizationId,
                organizationUserId).stream().findFirst();
    }

    @Override
    public List<EntryRow> findPersonalEntries(
            long tenantId,
            long organizationId,
            long walletId,
            PersonalWalletEntryAudience audience,
            Long beforeEntrySequenceNo,
            int fetchLimit) {
        boolean hideWithdrawalTransfers = switch (
                Objects.requireNonNull(audience, "audience")) {
            case ORDINARY_USER -> true;
            case AUDIT -> false;
        };
        String visibility = hideWithdrawalTransfers
                ? "AND event_type NOT IN (?, ?)\n"
                : "";
        String anchor = beforeEntrySequenceNo == null
                ? ""
                : "AND entry_sequence_no < ?\n";
        List<Object> arguments = new ArrayList<>();
        arguments.add(tenantId);
        arguments.add(organizationId);
        arguments.add(walletId);
        if (hideWithdrawalTransfers) {
            arguments.add(WITHDRAWAL_FREEZE);
            arguments.add(WITHDRAWAL_RELEASED);
        }
        if (beforeEntrySequenceNo != null) {
            arguments.add(beforeEntrySequenceNo);
        }
        arguments.add(fetchLimit);
        return jdbc.query(
                ENTRY_COLUMNS + """
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND wallet_id = ?
                        """ + visibility + anchor + """
                        ORDER BY entry_sequence_no DESC
                        LIMIT ?
                        """,
                JdbcWalletReadRepository::entryRow,
                arguments.toArray());
    }

    @Override
    public long currentOrganizationHighWatermark(
            long tenantId,
            long organizationId) {
        List<Long> values = jdbc.query("""
                        SELECT last_visibility_sequence_no
                        FROM fund_organization_wallet_entry_counter
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """,
                (rs, ignored) -> rs.getLong(
                        "last_visibility_sequence_no"),
                tenantId,
                organizationId);
        return values.isEmpty() ? 0L : values.getFirst();
    }

    @Override
    public List<EntryRow> findOrganizationEntries(
            OrganizationPageQuery query) {
        StringBuilder sql = new StringBuilder(ENTRY_COLUMNS)
                .append("""
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND visibility_sequence_no <= ?
                        """);
        List<Object> arguments = new ArrayList<>();
        arguments.add(query.tenantId());
        arguments.add(query.organizationId());
        arguments.add(query.highWatermark());
        if (query.organizationUserId() != null) {
            sql.append("AND organization_user_id = ?\n");
            arguments.add(query.organizationUserId());
        }
        if (query.entryType() != null) {
            sql.append("AND event_type = ?\n");
            arguments.add(query.entryType());
        }
        if (query.occurredFrom() != null) {
            sql.append("AND occurred_at >= ?\n");
            arguments.add(query.occurredFrom());
        }
        if (query.occurredTo() != null) {
            sql.append("AND occurred_at < ?\n");
            arguments.add(query.occurredTo());
        }
        if (query.sourceNo() != null) {
            sql.append("AND source_no = ?\n");
            arguments.add(query.sourceNo());
        }
        if (query.lastOccurredAt() != null) {
            sql.append("""
                    AND (
                        occurred_at < ?
                        OR (
                            occurred_at = ?
                            AND organization_user_uid < ?
                        )
                        OR (
                            occurred_at = ?
                            AND organization_user_uid = ?
                            AND entry_sequence_no < ?
                        )
                    )
                    """);
            arguments.add(query.lastOccurredAt());
            arguments.add(query.lastOccurredAt());
            arguments.add(
                    query.lastOrganizationUserUid().toString());
            arguments.add(query.lastOccurredAt());
            arguments.add(
                    query.lastOrganizationUserUid().toString());
            arguments.add(query.lastEntrySequenceNo());
        }
        sql.append("""
                ORDER BY occurred_at DESC,
                         organization_user_uid DESC,
                         entry_sequence_no DESC
                LIMIT ?
                """);
        arguments.add(query.fetchLimit());
        return jdbc.query(
                sql.toString(),
                JdbcWalletReadRepository::entryRow,
                arguments.toArray());
    }

    private static EntryRow entryRow(ResultSet rs, int ignored)
            throws SQLException {
        return new EntryRow(
                UUID.fromString(rs.getString("entry_uid")),
                UUID.fromString(
                        rs.getString("organization_user_uid")),
                rs.getLong("entry_sequence_no"),
                rs.getString("event_type"),
                rs.getLong("available_delta_cent"),
                rs.getLong("frozen_delta_cent"),
                rs.getLong("available_after_cent"),
                rs.getLong("frozen_after_cent"),
                rs.getString("source_type"),
                rs.getString("source_no"),
                rs.getObject("occurred_at", LocalDateTime.class));
    }
}
