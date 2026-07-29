package org.enveloping.ecobin.funds.application.startdelivery;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
class JdbcStartDeliveryWalletLockRepository
        implements StartDeliveryWalletLockRepository {

    static final String LOCK_WALLET_SQL = """
            SELECT wallet_uid, available_balance_cent,
                   delivery_gate_state
            FROM fund_user_wallet
            WHERE tenant_id = ?
              AND organization_id = ?
              AND organization_user_id = ?
            FOR UPDATE
            """;

    private final JdbcTemplate jdbc;

    JdbcStartDeliveryWalletLockRepository(JdbcTemplate jdbc) {
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
                                UUID.fromString(
                                        rs.getString("wallet_uid")),
                                rs.getLong("available_balance_cent"),
                                rs.getString("delivery_gate_state")),
                        tenantId,
                        organizationId,
                        organizationUserId)
                .stream()
                .findFirst();
    }
}
