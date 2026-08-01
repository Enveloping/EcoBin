package org.enveloping.ecobin.funds.application.startdelivery;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
class JdbcDeliveryWalletQualificationQueryRepository
        implements DeliveryWalletQualificationQueryRepository {

    static final String FIND_CURRENT_WALLET_SQL = """
            SELECT available_balance_cent, delivery_gate_state
            FROM fund_user_wallet
            WHERE tenant_id = ?
              AND organization_id = ?
              AND organization_user_id = ?
            """;

    private final JdbcTemplate jdbc;

    JdbcDeliveryWalletQualificationQueryRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<WalletQualificationRow> findCurrentWallet(
            long tenantId,
            long organizationId,
            long organizationUserId) {
        return jdbc.query(
                        FIND_CURRENT_WALLET_SQL,
                        (rs, ignored) -> new WalletQualificationRow(
                                rs.getLong("available_balance_cent"),
                                rs.getString("delivery_gate_state")),
                        tenantId,
                        organizationId,
                        organizationUserId)
                .stream()
                .findFirst();
    }
}
