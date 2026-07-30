package org.enveloping.ecobin.funds.application.deliveryrevision;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Repository;

import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.Optional;

@Repository
class JdbcDeliveryRevisionDeltaRepository
        implements DeliveryRevisionDeltaRepository {

    static final String LOCK_WALLET_SQL = """
            SELECT id, available_balance_cent,
                   frozen_withdrawal_cent,
                   last_entry_sequence_no,
                   delivery_gate_state,
                   delivery_gate_threshold_snapshot_cent,
                   delivery_gate_trigger_entry_id,
                   delivery_gate_latched_at,
                   lock_version
            FROM fund_user_wallet
            WHERE tenant_id = ?
              AND organization_id = ?
              AND organization_user_id = ?
            FOR UPDATE
            """;

    static final String LOCK_ORGANIZATION_COUNTER_SQL = """
            SELECT last_visibility_sequence_no, lock_version
            FROM fund_organization_wallet_entry_counter
            WHERE tenant_id = ?
              AND organization_id = ?
            FOR UPDATE
            """;

    static final String LOCK_ACTIVE_WITHDRAWAL_SQL = """
            SELECT withdrawal_order_id
            FROM fund_active_withdrawal
            WHERE tenant_id = ?
              AND organization_id = ?
              AND wallet_id = ?
            FOR UPDATE
            """;

    static final String LOCK_WITHDRAWAL_ORDER_SQL = """
            SELECT id, business_state,
                   negative_balance_pause,
                   post_boundary_risk,
                   pre_channel_block_reason,
                   channel_boundary_at,
                   lock_version
            FROM fund_withdrawal_order
            WHERE tenant_id = ?
              AND organization_id = ?
              AND wallet_id = ?
              AND id = ?
            FOR UPDATE
            """;

    static final String INSERT_WALLET_ENTRY_SQL = """
            INSERT INTO fund_user_wallet_entry (
                entry_uid,
                tenant_id, organization_id,
                wallet_id, organization_user_id,
                entry_sequence_no, visibility_sequence_no,
                event_type,
                available_delta_cent,
                available_before_cent,
                available_after_cent,
                frozen_delta_cent,
                frozen_before_cent,
                frozen_after_cent,
                delivery_revision_id,
                withdrawal_order_id,
                adjustment_id,
                fund_phase,
                occurred_at,
                created_at
            ) VALUES (
                ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?,
                ?,
                ?,
                ?,
                0,
                ?,
                ?,
                ?,
                NULL,
                NULL,
                NULL,
                ?,
                ?
            )
            """;

    static final String ADVANCE_ORGANIZATION_COUNTER_SQL = """
            UPDATE fund_organization_wallet_entry_counter
            SET last_visibility_sequence_no = ?,
                lock_version = lock_version + 1,
                updated_at = ?
            WHERE tenant_id = ?
              AND organization_id = ?
              AND last_visibility_sequence_no = ?
              AND lock_version = ?
            """;

    static final String UPDATE_WALLET_SQL = """
            UPDATE fund_user_wallet
            SET available_balance_cent = ?,
                last_entry_sequence_no = ?,
                delivery_gate_state = ?,
                delivery_gate_threshold_snapshot_cent = ?,
                delivery_gate_trigger_entry_id = ?,
                delivery_gate_latched_at = ?,
                lock_version = lock_version + 1,
                updated_at = ?
            WHERE tenant_id = ?
              AND organization_id = ?
              AND organization_user_id = ?
              AND id = ?
              AND last_entry_sequence_no = ?
              AND lock_version = ?
            """;

    static final String UPDATE_WITHDRAWAL_ORDER_SQL = """
            UPDATE fund_withdrawal_order
            SET negative_balance_pause = ?,
                post_boundary_risk = ?,
                lock_version = lock_version + 1,
                updated_at = ?
            WHERE tenant_id = ?
              AND organization_id = ?
              AND wallet_id = ?
              AND id = ?
              AND lock_version = ?
            """;

    private final JdbcTemplate jdbc;

    JdbcDeliveryRevisionDeltaRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<WalletRow> lockWallet(
            long tenantId,
            long organizationId,
            long organizationUserId) {
        return jdbc.query(
                        LOCK_WALLET_SQL,
                        (rs, ignored) -> new WalletRow(
                                rs.getLong("id"),
                                rs.getLong("available_balance_cent"),
                                rs.getLong("frozen_withdrawal_cent"),
                                rs.getLong("last_entry_sequence_no"),
                                rs.getString("delivery_gate_state"),
                                nullableLong(
                                        rs,
                                        "delivery_gate_threshold_snapshot_cent"),
                                nullableLong(
                                        rs,
                                        "delivery_gate_trigger_entry_id"),
                                nullableDateTime(
                                        rs,
                                        "delivery_gate_latched_at"),
                                rs.getLong("lock_version")),
                        tenantId,
                        organizationId,
                        organizationUserId)
                .stream()
                .findFirst();
    }

    @Override
    public Optional<OrganizationCounterRow> lockOrganizationCounter(
            long tenantId,
            long organizationId) {
        return jdbc.query(
                        LOCK_ORGANIZATION_COUNTER_SQL,
                        (rs, ignored) -> new OrganizationCounterRow(
                                rs.getLong(
                                        "last_visibility_sequence_no"),
                                rs.getLong("lock_version")),
                        tenantId,
                        organizationId)
                .stream()
                .findFirst();
    }

    @Override
    public Optional<ActiveWithdrawalRow> lockActiveWithdrawal(
            long tenantId,
            long organizationId,
            long walletId) {
        return jdbc.query(
                        LOCK_ACTIVE_WITHDRAWAL_SQL,
                        (rs, ignored) -> new ActiveWithdrawalRow(
                                rs.getLong("withdrawal_order_id")),
                        tenantId,
                        organizationId,
                        walletId)
                .stream()
                .findFirst();
    }

    @Override
    public Optional<WithdrawalOrderRow> lockWithdrawalOrder(
            long tenantId,
            long organizationId,
            long walletId,
            long withdrawalOrderId) {
        return jdbc.query(
                        LOCK_WITHDRAWAL_ORDER_SQL,
                        (rs, ignored) -> new WithdrawalOrderRow(
                                rs.getLong("id"),
                                rs.getString("business_state"),
                                rs.getBoolean(
                                        "negative_balance_pause"),
                                rs.getBoolean("post_boundary_risk"),
                                rs.getString(
                                        "pre_channel_block_reason"),
                                nullableDateTime(
                                        rs,
                                        "channel_boundary_at"),
                                rs.getLong("lock_version")),
                        tenantId,
                        organizationId,
                        walletId,
                        withdrawalOrderId)
                .stream()
                .findFirst();
    }

    @Override
    public long insertWalletEntry(WalletEntryInsert insert) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        PreparedStatementCreator creator = connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    INSERT_WALLET_ENTRY_SQL,
                    Statement.RETURN_GENERATED_KEYS);
            int index = 1;
            statement.setString(
                    index++,
                    insert.entryUid().toString());
            statement.setLong(index++, insert.tenantId());
            statement.setLong(index++, insert.organizationId());
            statement.setLong(index++, insert.walletId());
            statement.setLong(
                    index++,
                    insert.organizationUserId());
            statement.setLong(
                    index++,
                    insert.entrySequenceNo());
            statement.setLong(
                    index++,
                    insert.visibilitySequenceNo());
            statement.setString(index++, insert.eventType());
            statement.setLong(
                    index++,
                    insert.availableDeltaCent());
            statement.setLong(
                    index++,
                    insert.availableBeforeCent());
            statement.setLong(
                    index++,
                    insert.availableAfterCent());
            statement.setLong(
                    index++,
                    insert.frozenBeforeCent());
            statement.setLong(
                    index++,
                    insert.frozenBeforeCent());
            statement.setLong(
                    index++,
                    insert.deliveryRevisionId());
            statement.setObject(index++, insert.occurredAt());
            statement.setObject(index, insert.createdAt());
            return statement;
        };
        int affected = jdbc.update(creator, keyHolder);
        Number generatedKey = keyHolder.getKey();
        if (affected != 1
                || generatedKey == null
                || generatedKey.longValue() <= 0) {
            throw invariant(
                    "insert wallet entry affected "
                            + affected + " rows");
        }
        return generatedKey.longValue();
    }

    @Override
    public void advanceOrganizationCounter(
            OrganizationCounterUpdate update) {
        requireSingle(
                jdbc.update(
                        ADVANCE_ORGANIZATION_COUNTER_SQL,
                        update.nextVisibilitySequenceNo(),
                        update.updatedAt(),
                        update.tenantId(),
                        update.organizationId(),
                        update.previousVisibilitySequenceNo(),
                        update.expectedLockVersion()),
                "advance organization wallet-entry counter");
    }

    @Override
    public void updateWallet(WalletUpdate update) {
        requireSingle(
                jdbc.update(
                        UPDATE_WALLET_SQL,
                        update.availableBalanceCent(),
                        update.nextEntrySequenceNo(),
                        update.deliveryGateState(),
                        update.deliveryGateThresholdSnapshotCent(),
                        update.deliveryGateTriggerEntryId(),
                        update.deliveryGateLatchedAt(),
                        update.updatedAt(),
                        update.tenantId(),
                        update.organizationId(),
                        update.organizationUserId(),
                        update.walletId(),
                        update.previousEntrySequenceNo(),
                        update.expectedLockVersion()),
                "update delivery revision wallet projection");
    }

    @Override
    public void updateWithdrawalOrder(
            WithdrawalOrderUpdate update) {
        requireSingle(
                jdbc.update(
                        UPDATE_WITHDRAWAL_ORDER_SQL,
                        update.negativeBalancePause() ? 1 : 0,
                        update.postBoundaryRisk() ? 1 : 0,
                        update.updatedAt(),
                        update.tenantId(),
                        update.organizationId(),
                        update.walletId(),
                        update.withdrawalOrderId(),
                        update.expectedLockVersion()),
                "update withdrawal balance flags");
    }

    private static Long nullableLong(
            ResultSet resultSet,
            String column) throws SQLException {
        long value = resultSet.getLong(column);
        return resultSet.wasNull() ? null : value;
    }

    private static LocalDateTime nullableDateTime(
            ResultSet resultSet,
            String column) throws SQLException {
        return resultSet.getObject(column, LocalDateTime.class);
    }

    private static void requireSingle(
            int affected,
            String operation) {
        if (affected != 1) {
            throw invariant(
                    operation + " affected "
                            + affected + " rows");
        }
    }

    private static IllegalStateException invariant(String message) {
        return new IllegalStateException(
                "funds invariant violated: " + message);
    }
}
